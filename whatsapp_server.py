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
from custom.functions.helper_functions import filter_reviews_by_rating, send_restaurant_advisor_template
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

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

#configuration for response templates
response_config_path = os.environ.get("RESPONSE_CONFIG_PATH")
#print(f"Response config path: {response_config_path}")

# Initialize the MongoDB client
#db = MongoDB()

# Initialize the WhatsApp agent - this is PERSISTENT across requests
whatsapp_system = WhatsAppAgent(my_llm)

# Initialize the response generator agent
response_system = ResponseGeneratorAgent(my_llm, response_config_path)

# Initialize the agent advice agent
agent_advice_system = AgentAdviceAgent(my_llm)

# Initialize the scheduler
scheduler = AsyncIOScheduler()

# Add a dictionary to store pre-loaded reviews
preloaded_reviews = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start scheduler and initialize jobs
    scheduler.start()
    #setup_review_reminder_scheduler()
    #setup_inactivity_checker_scheduler()
    
    # Pre-load reviews for faster demonstration
    await preload_reviews(RESTAURANT_NAME, NUM_REVIEWS)
    
    yield  # Server is running and handling requests
    
    # Shutdown: Clean up scheduler
    scheduler.shutdown()

async def preload_reviews(restaurant_name: str, num_reviews: int):
    """Preload reviews when the server starts"""
    try:
        logger.info(f"Preloading reviews for {restaurant_name}")
        
        # Run the review workflow
        crew_output = run_review_workflow(restaurant_name, num_reviews)
        
        # Use the tool to process the CrewAI output
        process_output_tool = whatsapp_system.whatsapp_agent.tools[4]  # Make sure this index is correct
        processed_json_str = process_output_tool.run(crew_output)
        
        # Process the results
        json_result_original = json.loads(processed_json_str)
        json_result, removed_count = filter_reviews_by_rating(json_result_original)
        
        # Store in global variable for later use
        global preloaded_reviews
        preloaded_reviews = {
            'json_result': json_result,
            'json_result_original': json_result_original,
            'removed_count': removed_count
        }
        
        logger.info(f"Successfully preloaded {len(json_result.get('analyzed_reviews', []))} reviews")
        
    except Exception as e:
        logger.error(f"Error in preloading reviews: {str(e)}")

# Create FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

RESTAURANT_NAME = 'Zareen\'s'
NUM_REVIEWS = 10

@app.post("/webhook")
async def whatsapp_webhook(
    Body: str = Form(...), 
    From: str = Form(...),
    background_tasks: BackgroundTasks = None
):
    logger.info(f"Received message from {From}: {Body}")
    
    # Add the user to active_sessions if not already there
    whatsapp_system.active_sessions[From] = True
    
    # Update last activity time
    whatsapp_system.last_activity[From] = datetime.now()
    
    # Process the incoming message with our new LLM-based intent processor
    # Note: Tool index may need adjustment based on the order in the initialization
    process_message_tool = whatsapp_system.whatsapp_agent.tools[1]  # Index for the LLM intent processor
    intent_response = process_message_tool.run(
        user_id=From,
        message_text=Body
    )
    
    logger.info(f"Intent from message: {intent_response}")
    
    # Handle the welcome/initial message differently - automatically fetch reviews
    if intent_response == "CONVERSATION:WELCOME":
        # Send welcome message
        welcome_message = """
        👋 *Hello [Business Owner]!*
        
        Welcome to our Review Manager. I'm automatically fetching your latest reviews now.
        Please wait a moment while I prepare them for your review.
        """
        
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=From,
            message=welcome_message
        )
        
        # Save to memory
        memory = whatsapp_system.get_user_memory(From)
        memory.save_context({"input": Body}, {"output": welcome_message})
        
        # Automatically start fetching reviews in the background
        background_tasks.add_task(
            fetch_reviews_background, 
            manager_phone=From.replace("whatsapp:", ""),
            restaurant_name=RESTAURANT_NAME,
            num_reviews=NUM_REVIEWS
        )
        
    # Handle APPROVED command
    elif intent_response.startswith("APPROVED:NEXT_REVIEW"):
        # Send confirmation message about approval
        confirmation_message = "✅ Response approved! "
        whatsapp_system.whatsapp_agent.tools[0].run(
        to=From,
        message=confirmation_message
            )
        
        # Add timer/pause here
        await asyncio.sleep(25)  # Pause for 3 seconds before showing next review
 
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


            # *All reviews have been processed!*
            
            # *Summary:*
            # Total Reviews: {summary_data.get('total', 0)}
            # Approved: {summary_data.get('approved', 0)}
            
            # Thank you for reviewing these responses. The approved responses will be sent to customers.
            
            completion_message = f"""
            *No New Reviews Found!*

            """
            
            whatsapp_system.whatsapp_agent.tools[0].run(
                to=From,
                message=completion_message
            )
            
            # Reset the user's review state after completion
            whatsapp_system.reset_user_review_state(From)
        
    # Handle UNCLEAR command
    elif intent_response.startswith("UNCLEAR:"):
        unclear_message = """
        I'm not sure I understood your response. Please let me know if you:
        
        - Want to approve this response (say "approve" or "looks good")
        - Want to revise it (provide specific feedback)
        
        What would you like to do with this review response?
        """
        
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=From,
            message=unclear_message
        )
        
    # Handle REVISION command
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
                
                # Send the revised response
                response_message = f"""
                I've revised the response based on your feedback:
                
                *Revised Response:*
                {revised_response}
                
                Does this look good now? Let me know if you approve or need further revisions.
                """
                
                whatsapp_system.whatsapp_agent.tools[0].run(
                    to=From,
                    message=response_message
                )
            except Exception as e:
                error_message = f"Sorry, I encountered an error while revising the response: {str(e)}"
                whatsapp_system.whatsapp_agent.tools[0].run(
                    to=From,
                    message=error_message
                )
    
    # Handle ALL_COMPLETED command
    elif intent_response.startswith("COMMAND:ALL_COMPLETED"):
        # Get summary data for the completed reviews
        summary = whatsapp_system.whatsapp_agent.tools[2].run(
            action="summarize",
            user_id=From
        )
        summary_data = json.loads(summary)
        
        # Send a message informing the user that all reviews are processed
        completion_message = f"""
        *No New Reviews Found!*

        *All reviews have been processed!*
        
        *Summary:*
        Total Reviews: {summary_data.get('total', 0)}
        Approved: {summary_data.get('approved', 0)}
        
        Thank you for reviewing these responses. The approved responses will be sent to customers.
        
        You can type any message to start a new session.
        """
        
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=From,
            message=completion_message
        )
    
    return {"status": "success", "message": "Message processed"}

async def fetch_reviews_background(manager_phone: str, restaurant_name: str, num_reviews: int):
    """Background task to fetch and analyze reviews"""
    try:
        # Format the phone number correctly for Twilio
        formatted_phone = f"whatsapp:{manager_phone}"
        
        # Check if we have preloaded reviews
        global preloaded_reviews
        if preloaded_reviews:
            logger.info("Using preloaded reviews")
            json_result = preloaded_reviews['json_result']
            json_result_original = preloaded_reviews['json_result_original']
            removed_count = preloaded_reviews['removed_count']
        else:
            # Run the review workflow if no preloaded reviews exist
            logger.info("No preloaded reviews, fetching now")
            crew_output = run_review_workflow(restaurant_name, num_reviews)
            
            # Process the output
            process_output_tool = whatsapp_system.whatsapp_agent.tools[4]  # Make sure index is correct
            processed_json_str = process_output_tool.run(crew_output)
            
            # Save the results
            json_result_original = json.loads(processed_json_str)
            json_result, removed_count = filter_reviews_by_rating(json_result_original)
        
        # Initialize the review session
        whatsapp_system.review_data[formatted_phone] = json_result
        whatsapp_system.current_indices[formatted_phone] = 0
        whatsapp_system.review_states[formatted_phone] = 'initialized'

        # Initialize approval status
        reviews_original = json_result_original.get('analyzed_reviews', [])
        reviews = json_result.get('analyzed_reviews', [])
        for review in reviews:
            review['approval_status'] = 'pending'

        # Get counts of different types of reviews
        total_reviews = len(reviews_original)
        high_rated_reviews = removed_count
        pending_reviews = total_reviews - high_rated_reviews

        # Send message about review count
        await asyncio.sleep(6)

        review_start_message = f"""
        I've analyzed your recent reviews:
        
        📊 *Review Analysis*
        • Total Reviews: {total_reviews}
        • High-Rated Reviews (already good): {high_rated_reviews}
        • Reviews Needing Responses: {pending_reviews}
        
        Let's start reviewing responses for the lower-rated reviews. I'll show you each one, and you can simply approve it or suggest changes.
        """
        
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=formatted_phone,
            message=review_start_message
        )
        
        # Wait a moment before sending the first review
        await asyncio.sleep(20)
        
        # Send the first review automatically
        if pending_reviews > 0:
            # Use our updated send_review_for_approval function
            send_review_for_approval(whatsapp_system, formatted_phone, 0)
        else:
            whatsapp_system.whatsapp_agent.tools[0].run(
                to=formatted_phone,
                message="All your reviews already have high ratings. Great job with your customer service!"
            )
            whatsapp_system.review_states[formatted_phone] = 'completed'
        
    except Exception as e:
        logger.error(f"Error in fetch_reviews_background: {str(e)}")
        
        # Notify the manager of the error
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=f"whatsapp:{manager_phone}",
            message=f"I encountered an error while fetching reviews: {str(e)}"
        )
        
        return {"status": "error", "message": str(e)}

def send_review_for_approval(whatsapp_system, user_phone, review_idx):
    """Send a review for approval without buttons or review numbering"""
    reviews = whatsapp_system.review_data.get(user_phone, {}).get('analyzed_reviews', [])
    if review_idx < len(reviews):
        review = reviews[review_idx]
        
        # Format the star rating
        stars = "⭐" * int(review.get('rating', 3))
        
        # Send the review details without "Review X of Y" heading
        review_message = f"""
        *From:* {review.get('author', 'Customer')}
        *Rating:* {stars}
        
        *Original Review:*
        "{review.get('text', '')}"
        
        *Suggested Response:*
        {review.get('response', '')}
        
        Does this response look good? Please let me know if you approve or if you'd like any changes.
        """
        
        # Send review details
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=user_phone,
            message=review_message
        )
        
        return True
    return False

async def send_review_reminder(manager_phone: str):
    """Send a reminder to complete pending reviews"""
    formatted_phone = f"whatsapp:{manager_phone}"
    print("here")
    
    reminder_message = """
    📝 *Review Session Reminder*
    
    You have pending reviews waiting for your attention.
    """
    
    whatsapp_system.whatsapp_agent.tools[0].run(
        to=formatted_phone,
        message=reminder_message
    )
    

async def check_and_send_initial_message(manager_phone: str):
    """Check if manager needs initial message and send if appropriate"""
    formatted_phone = f"whatsapp:{manager_phone}"
    
    # Check if there's an active review session
    review_state = whatsapp_system.review_states.get(formatted_phone)
    
    # Only send welcome message if not in an active review session
    if review_state != 'initialized':
        # Updated welcome message that indicates automatic fetching
        welcome_message = """
        👋 *Hello [Business Owner]!*
        
        Welcome to our Restaurant Review Manager. I'm automatically fetching your latest reviews now.
        Please wait a moment while I prepare them for your review.
        """
        
        whatsapp_system.whatsapp_agent.tools[0].run(
            to=formatted_phone,
            message=welcome_message
        )
        
        # Automatically trigger review fetching
        await fetch_reviews_background(
            manager_phone=manager_phone,
            restaurant_name=RESTAURANT_NAME,
            num_reviews=NUM_REVIEWS
        )

def setup_review_reminder_scheduler():
    """Setup scheduler for checking inactive review sessions"""
    async def check_inactive_sessions():
        current_time = datetime.now()
        
        # Use active_sessions instead of review_states
        for phone in whatsapp_system.active_sessions.keys():
            print("here are the active sessions")
            print(whatsapp_system.active_sessions)
            
            # Check if there's an initialized review state
            if whatsapp_system.review_states.get(phone) == 'initialized':
                # Get last activity time
                last_activity = whatsapp_system.last_activity.get(phone, current_time)
                
                # If inactive for more than 1 hour
                if (current_time - last_activity) > timedelta(minutes=1):
                    await send_review_reminder(phone.replace("whatsapp:", ""))
    
    # Run every 15 minutes
    scheduler.add_job(
        check_inactive_sessions,
        trigger=IntervalTrigger(minutes=2),
        id='inactive_session_checker',
        replace_existing=True
    )

def setup_inactivity_checker_scheduler():
    """Setup scheduler for sending initial messages every 12 hours"""
    async def send_periodic_messages():
        # Get all known manager phones from active_sessions
        manager_phones = set()
        for phone in whatsapp_system.active_sessions.keys():
            manager_phones.add(phone.replace("whatsapp:", ""))
        
        # Send messages to each manager
        for phone in manager_phones:
            await check_and_send_initial_message(phone)
    
    # Run every 12 hours
    scheduler.add_job(
        send_periodic_messages,
        trigger=IntervalTrigger(minutes=3),
        id='periodic_message_sender',
        replace_existing=True
    )

@app.get("/health")
def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("whatsapp_server:app", host="0.0.0.0", port=8000, reload=True)