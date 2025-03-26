import os
import logging
import json
import asyncio
from fastapi import FastAPI, Form, Request, BackgroundTasks
from twilio.request_validator import RequestValidator
from dotenv import load_dotenv
import sys
import time
#from custom.MongoDB.mongodb_client import MongoDB
from custom.functions.helper_functions import filter_reviews_by_rating

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Execute the task with a temporary crew
from crewai import Crew
from crewai import Crew, LLM
from agents.whatsapp_agent import WhatsAppAgent
from main_new import run_review_workflow
from agents.response_generator_agent import ResponseGeneratorAgent
from agents.agent_advice import AgentAdviceAgent

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whatsapp_server")

# Initialize LLM
my_llm = LLM(
    api_key=os.environ["GOOGLE_API_KEY"],
    model="gemini/gemini-2.0-flash",
)

# Initialize the MongoDB client
#db = MongoDB()

# Initialize the WhatsApp agent - this is PERSISTENT across requests
whatsapp_system = WhatsAppAgent(my_llm)

# Initialize the response generator agent
response_system = ResponseGeneratorAgent(my_llm)

# Initialize the agent advice agent
agent_advice_system = AgentAdviceAgent(my_llm)

# Create FastAPI app
app = FastAPI()

RESTAURANT_NAME = 'kfc'
NUM_REVIEWS = 15

@app.post("/webhook")
async def whatsapp_webhook(
    Body: str = Form(...), 
    From: str = Form(...),
    background_tasks: BackgroundTasks = None
):
    logger.info(f"Received message from {From}: {Body}")
    
    # Process the incoming message
    process_message_tool = whatsapp_system.whatsapp_agent.tools[1]
    intent_response = process_message_tool.run(
        user_id=From,
        message_text=Body
    )
    
    logger.info(f"Intent from message: {intent_response}")
    
    # Handle different intents based on the response
    if intent_response == "COMMAND:FETCH_REVIEWS":
        # Start background task to fetch reviews
        background_tasks.add_task(
            fetch_reviews_background, 
            manager_phone=From.replace("whatsapp:", ""),  # Remove prefix for processing
            restaurant_name=RESTAURANT_NAME,
            num_reviews=NUM_REVIEWS
        )
        
        # Send immediate acknowledgment
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=From,
            message="I'm fetching the latest reviews for your restaurant. This may take a minute or two..."
        )
        
    elif intent_response.startswith("COMMAND:NEXT_REVIEW") or intent_response.startswith("COMMAND:CONTINUE_REVIEWS"):
        current_idx = whatsapp_system.current_indices.get(From, 0)
        send_review_for_approval(whatsapp_system, From, current_idx)
        
    elif intent_response.startswith("APPROVED:NEXT_REVIEW"):
        # Move to next review
        current_idx = whatsapp_system.current_indices.get(From, 0)
        whatsapp_system.current_indices[From] = current_idx + 1
        
        # Check if we have more reviews
        if send_review_for_approval(whatsapp_system, From, current_idx + 1):
            pass  # Successfully sent next review
        else:
            # No more reviews
            summary = whatsapp_system.whatsapp_agent.tools[2].run(
                action="summarize",
                user_id=From
            )
            whatsapp_system.review_states[From] = 'completed'
            summary_data = json.loads(summary)
            
            completion_message = f"""
            *All reviews have been processed!*
            
            *Summary:*
            Total Reviews: {summary_data.get('total', 0)}
            Approved: {summary_data.get('approved', 0)}
           
            
            Thank you for reviewing these responses. The approved responses will be sent to customers.
            """
            
            whatsapp_system.whatsapp_agent.tools[0].run(
                to=From,
                message=completion_message
            )

                        # Then send the ending button template
            ending_button_template_sid = "HXc38156470966a4e486740800455dcc00"
            whatsapp_system.whatsapp_agent.tools[5].run(
                to=From,
                content_sid=ending_button_template_sid,
                variables={}
            )
        
    elif intent_response.startswith("FEEDBACK_NEEDED:"):
        feedback_request = intent_response.replace("FEEDBACK_NEEDED:", "").strip()
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=From,
            message=f"{feedback_request}"
        )
        
    elif intent_response.startswith("REVISION:"):
        feedback = intent_response.replace("REVISION:", "").strip()
        
        # Process revision request
        current_idx = whatsapp_system.current_indices.get(From, 0)
        reviews = whatsapp_system.review_data.get(From, {}).get('analyzed_reviews', [])
        
        if current_idx < len(reviews):
            # Get current review info
            current_review = reviews[current_idx]
            review_text = current_review.get('text', '')
            original_response = current_review.get('response', '')
            
            try:
                # Create and execute revision task
                revision_task = response_system.create_feedback_revision_task(
                    review_text=review_text,
                    original_response=original_response,
                    manager_feedback=feedback
                )
                
                temp_crew = Crew(
                    agents=[response_system.response_agent],
                    tasks=[revision_task],
                    verbose=False
                )
                
                # Get the revised response
                crew_output = temp_crew.kickoff()
                
                # Extract the response text
                if hasattr(crew_output, 'raw'):
                    revised_response = crew_output.raw
                elif hasattr(crew_output, 'outputs') and len(crew_output.outputs) > 0:
                    revised_response = crew_output.outputs[0]
                elif hasattr(crew_output, 'output'):
                    revised_response = crew_output.output
                else:
                    revised_response = str(crew_output)
                
                # Ensure it's a string
                revised_response = str(revised_response)
                
                # Update the review with revised response
                reviews[current_idx]['response'] = revised_response
                
                # Send the revised response first as a regular message
                response_message = f"""
                I've revised the response based on your feedback:
                
                *Revised Response:*
                {revised_response}
                """
                
                whatsapp_system.whatsapp_agent.tools[0].run(
                    to=From,
                    message=response_message
                )
                
                # Then send the action buttons template
                review_action_template_sid = "HXdefe78f44b33997898bda8101784b2f3"  # Your template SID
                whatsapp_system.whatsapp_agent.tools[5].run(
                    to=From,
                    content_sid=review_action_template_sid,
                    variables={}
                )

                # # Save the review data to MongoDB
                # result_update = db.update_review_response(
                #     restaurant_name=RESTAURANT_NAME,
                #     author=reviews[current_idx]['author'],
                #     text_prefix=reviews[current_idx]['text'],
                #     response=revised_response
                # )
                # print(f"MongoDB update result: {result_update}")
            except Exception as e:
                # Error handling...
                pass
        
    elif intent_response.startswith("COMMAND:SHOW_SUMMARY"):
        # Show summary
        summary = whatsapp_system.whatsapp_agent.tools[2].run(
            action="summarize",
            user_id=From
        )
        summary_data = json.loads(summary)
        
        summary_message = f"""
        *Review Progress Summary*
        
        Total Reviews: {summary_data.get('total', 0)}
        Approved: {summary_data.get('approved', 0)}
        Pending: {summary_data.get('pending', 0)}
        Needs Revision: {summary_data.get('needs_revision', 0)}
        Current Review: {summary_data.get('current_index', 0) + 1} of {summary_data.get('total', 0)}
        
        Type "continue" to resume reviewing
        """
        
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=From,
            message=summary_message
        )
        
    elif intent_response.startswith("COMMAND:ALL_COMPLETED"):
        # Get summary data for the completed reviews
        summary = whatsapp_system.whatsapp_agent.tools[2].run(
            action="summarize",
            user_id=From
        )
        summary_data = json.loads(summary)
        
        # Send a message informing the user that all reviews are processed
        completion_message = f"""
        *All reviews have been processed!*
        
        *Summary:*
        Total Reviews: {summary_data.get('total', 0)}
        Approved: {summary_data.get('approved', 0)}
        
        Thank you for reviewing these responses. The approved responses will be sent to customers.
        """
        
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=From,
            message=completion_message
        )
        #time.sleep(2)

        # Then send the ending button template
        ending_button_template_sid = "HXc38156470966a4e486740800455dcc00"
        whatsapp_system.whatsapp_agent.tools[5].run(
            to=From,
            content_sid=ending_button_template_sid,
            variables={}
        )
        
    elif intent_response.startswith("UNCLEAR:"):
        # Send reminder about number options
        unclear_message = intent_response.replace("UNCLEAR:", "").strip()
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=From,
            message=unclear_message
        )
        
    elif intent_response.startswith("CONVERSATION:"): 
        # Get your content SID from the successfully created template
        restaurant_advisor_template_sid = "HX48b4234fbf7194f89b540dbe648585de"
        manager_name = "Areeb"
        
        # Try to send the template message with buttons
        try:
            if restaurant_advisor_template_sid:
                # Use our new template tool
                template_tool = whatsapp_system.whatsapp_agent.tools[5]  # Index 5 for the template tool
                template_tool.run(
                    to=From,
                    content_sid=restaurant_advisor_template_sid,
                    variables={"1": manager_name}
                )
            else:
                # Fallback to regular message if template SID is not configured
                fallback_message = "I'm here to help with your review management. You can say 'get reviews' to fetch new reviews, 'continue' to review responses, or 'summary' to see your progress."
                whatsapp_system.whatsapp_agent.tools[0].run(
                    to=From,
                    message=fallback_message
                )
        except Exception as e:
            logger.error(f"Error sending template message: {str(e)}")
            # Fallback to regular message if template fails
            fallback_message = "I'm here to help with your review management. You can say 'get reviews' to fetch new reviews, 'continue' to review responses, or 'summary' to see your progress."
            whatsapp_system.whatsapp_agent.tools[0].run(
                to=From,
                message=fallback_message
            )
        
        # Add to memory
        memory = whatsapp_system.get_user_memory(From)
        memory.save_context({"input": Body}, {"output": "Sent template with options"})
        
    elif intent_response.startswith("COMMAND:AGENT_ADVICE"):
        # Send welcome message for agent advice
        welcome_message = agent_advice_system.get_welcome_message()
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=From,
            message=welcome_message
        )
        
        # Save to memory
        memory = agent_advice_system.get_user_memory(From)
        memory.save_context(
            {"input": Body}, 
            {"output": welcome_message}
        )
    
    elif intent_response.startswith("AGENT_ADVICE:"):
        # Extract the message for the agent
        advice_message = intent_response.replace("AGENT_ADVICE:", "").strip()
        
        # Get response from the agent advice system
        response = agent_advice_system.handle_advice_request(From, advice_message)
        
        # Check if we need to exit agent advice mode
        if response.startswith("EXIT:"):
            exit_message = response.replace("EXIT:", "").strip()
            whatsapp_system.whatsapp_agent.tools[0].run(
                to=From,
                message=exit_message
            )
            
            # Wait a moment before sending the template
            time.sleep(2)
            
            # Send the ending button template to return to main options
            ending_button_template_sid = "HXc38156470966a4e486740800455dcc00"
            whatsapp_system.whatsapp_agent.tools[5].run(
                to=From,
                content_sid=ending_button_template_sid,
                variables={}
            )
        else:
            # Send the agent advice response
            whatsapp_system.whatsapp_agent.tools[0].run(
                to=From,
                message=response
            )
    
    return {"status": "success", "message": "Message processed"}

async def fetch_reviews_background(manager_phone: str, restaurant_name: str, num_reviews: int):
    """Background task to fetch and analyze reviews"""
    try:
        # Run the review workflow
        crew_output = run_review_workflow(restaurant_name, num_reviews)
        #print(f"review_result: {review_result}")  
        #                                                                                                                                                                           
         # Use the new tool to process the CrewAI output
        process_output_tool = whatsapp_system.whatsapp_agent.tools[4]  # The tool we just added
        processed_json_str = process_output_tool.run(crew_output)
        
        # Save the results to the WhatsApp agent's state
        print("json before \n")
        json_result_original = json.loads(processed_json_str)
        json_result, removed_count = filter_reviews_by_rating(json_result_original)

        print("json after \n")
        print(f"Removed {removed_count} reviews")

        # Format the phone number correctly for Twilio
        formatted_phone = f"whatsapp:{manager_phone}"
        print("formatted_phone \n")
        # Initialize the review session
        whatsapp_system.review_data[formatted_phone] = json_result
        whatsapp_system.current_indices[formatted_phone] = 0
        whatsapp_system.review_states[formatted_phone] = 'initialized'
        print("review_data \n")

        # Initialize approval status
        reviews_original = json_result_original.get('analyzed_reviews', [])
        reviews = json_result.get('analyzed_reviews', [])
        for review in reviews:
            review['approval_status'] = 'pending'
        print("reviews \n")

        # Get counts of different types of reviews
        total_reviews = len(reviews_original)
        high_rated_reviews = removed_count
        pending_reviews = total_reviews - high_rated_reviews

        # Use the template message tool instead of plain text
        review_start_template_sid = "HXcf7e1cbf350f0695804d287fa71dff4d"  # Replace with your actual template SID
        template_variables = {
            "1": str(total_reviews),
            "2": str(high_rated_reviews),
            "3": str(pending_reviews)
        }

        whatsapp_system.whatsapp_agent.tools[5].run(
            to=formatted_phone,
            content_sid=review_start_template_sid,
            variables=template_variables
        )

        # # Save the review data to MongoDB
        # db_result = db.save_reviews(restaurant_name, reviews_original)
        # print(f"MongoDB save result: {db_result}")
        # print(f"Saved {db_result.get('saved_reviews', 0)} reviews to database")
        
    except Exception as e:
        logger.error(f"Error in fetch_reviews_background: {str(e)}")
        
        # Notify the manager of the error
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=f"whatsapp:{manager_phone}",
            message=f"I encountered an error while fetching reviews: {str(e)}"
        )
        
        return {"status": "error", "message": str(e)}

def send_review_for_approval(whatsapp_system, user_phone, review_idx):
    reviews = whatsapp_system.review_data.get(user_phone, {}).get('analyzed_reviews', [])
    if review_idx < len(reviews):
        review = reviews[review_idx]
        
        # Format the star rating
        stars = "⭐" * int(review.get('rating', 3))
        
        # First send the review details as a regular message
        review_message = f"""
        *Review {review_idx + 1} of {len(reviews)}*
        
        *From:* {review.get('author', 'Customer')}
        *Rating:* {stars}
        
        *Original Review:*
        "{review.get('text', '')}"
        
        *Suggested Response:*
        {review.get('response', '')}
        """
        
        # Send review details
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=user_phone,
            message=review_message
        )
        
        # Then send the action buttons template
        time.sleep(2)
        review_action_template_sid = "HXdefe78f44b33997898bda8101784b2f3"  # Replace with the SID you get after creating template
        whatsapp_system.whatsapp_agent.tools[5].run(
            to=user_phone,
            content_sid=review_action_template_sid,
            variables={}  # No variables needed for this template
        )
        
        return True
    return False

@app.get("/health")
def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("whatsapp_server:app", host="0.0.0.0", port=8000, reload=True)