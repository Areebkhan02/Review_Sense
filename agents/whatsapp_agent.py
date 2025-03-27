from crewai import Agent, Task, LLM
from langchain.tools import Tool
import json
import os
import logging
from typing import Dict, Any, List
from datetime import datetime
from twilio.rest import Client
from dotenv import load_dotenv
from langchain.memory import ConversationBufferMemory
from typing import Any

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whatsapp_agent")

class WhatsAppAgent:
    def __init__(self, llm):
        # Set up Twilio client for WhatsApp
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.twilio_number = os.getenv("TWILIO_WHATSAPP_NUMBER")
        self.client = Client(self.account_sid, self.auth_token)
        
        # State tracking
        self.active_sessions = {}
        self.review_data = {}
        self.current_indices = {}
        self.review_states = {}
        self.user_memories = {}
        self.last_command = {}
        
        # Create the WhatsApp agent
        self.whatsapp_agent = Agent(
            role='WhatsApp Review Approval Manager',
            goal='Facilitate manager approval of AI-generated review responses via WhatsApp',
            backstory="""You are an expert in customer service and restaurant management, 
            specializing in review response approval workflows. You communicate with restaurant 
            managers via WhatsApp to help them review and approve AI-generated responses to customer 
            reviews. You maintain context across conversations and can track the state of each review 
            throughout the approval process.""",
            verbose=True,
            allow_delegation=True,
            llm=llm,
            tools=[
                self.create_send_message_tool(),
                self.create_process_message_tool(),
                self.create_review_management_tool(),
                self.create_memory_management_tool(),
                self.create_crew_output_processor_tool(),
                self.create_template_message_tool()
            ]
        )
    
    def get_user_memory(self, user_id):
        """Retrieve or create a unique memory instance for each user."""
        if user_id not in self.user_memories:
            self.user_memories[user_id] = ConversationBufferMemory(memory_key="history")
        return self.user_memories[user_id]
    
    def create_send_message_tool(self) -> Tool:
        def send_whatsapp_message(to: str, message: str) -> str:
            """
            Send a WhatsApp message via Twilio
            
            Args:
                to: The recipient's WhatsApp number in format 'whatsapp:+1234567890'
                message: The message text to send
                
            Returns:
                str: Status of the message delivery
            """
            try:
                # Handle long messages by breaking them into chunks
                if len(message) > 1500:
                    chunks = [message[i:i+1500] for i in range(0, len(message), 1500)]
                    for i, chunk in enumerate(chunks):
                        sent_message = self.client.messages.create(
                            body=chunk + (f" ({i+1}/{len(chunks)})" if len(chunks) > 1 else ""),
                            from_=self.twilio_number,
                            to=to
                        )
                        logger.info(f"Sent message chunk {i+1}/{len(chunks)}, SID: {sent_message.sid}")
                        # Small delay between chunks to ensure order
                        if i < len(chunks) - 1:
                            import time
                            time.sleep(1)
                else:
                    print(f"authentication_account_sid: {self.account_sid}")
                    print(f"authentication_auth_token: {self.auth_token}")
                    print(f"authentication_twilio_number: {self.twilio_number}")
                    sent_message = self.client.messages.create(
                        body=message,
                        from_=self.twilio_number,
                        to=to
                    )
                    logger.info(f"Sent message SID: {sent_message.sid}")
                
                return f"Message sent successfully to {to}"
            except Exception as e:
                logger.error(f"Error sending WhatsApp message: {str(e)}")
                return f"Error sending WhatsApp message: {str(e)}"
        
        return Tool.from_function(
            func=send_whatsapp_message,
            name="SendWhatsAppTool",
            description="Sends a WhatsApp message to a restaurant manager. Use this to send responses, review information, and approval requests."
        )
    
    def create_process_message_tool(self) -> Tool:
        def process_whatsapp_message(user_id: str, message_text: str) -> str:
            """
            Process an incoming WhatsApp message from a manager
            
            Args:
                user_id: The manager's WhatsApp number
                message_text: The content of their message
                
            Returns:
                str: The recommended response or action to take
            """
            message_lower = message_text.lower()
            
            # Check for agent advice mode
            if hasattr(self, 'in_agent_advice_mode') and self.in_agent_advice_mode.get(user_id, False):
                # If they are in agent advice mode, all messages go to the advice agent
                # unless they specifically ask to exit
                if message_lower == "exit":
                    self.in_agent_advice_mode[user_id] = False
                    return "AGENT_ADVICE:exit"
                return f"AGENT_ADVICE:{message_text}"
            
            # Check for triggers to fetch new reviews or get agent advice
            if any(trigger in message_lower for trigger in ['fetch reviews', 'get reviews']):
                return "COMMAND:FETCH_REVIEWS"
            
            elif message_lower in ["agent advice", "agent_advice"] or "agent advice" in message_lower:
                # Mark user as in agent advice mode
                if not hasattr(self, 'in_agent_advice_mode'):
                    self.in_agent_advice_mode = {}
                self.in_agent_advice_mode[user_id] = True
                return "COMMAND:AGENT_ADVICE"
            
            # Check if we have an active review session
            if user_id in self.review_data:
                # Check if all reviews have been processed
                if self.review_states.get(user_id) == 'completed':
                    # User has completed all reviews but is still sending messages
                    if any(cmd in message_lower for cmd in ['summary']):
                        return "COMMAND:SHOW_SUMMARY"
                    else:
                        return "COMMAND:ALL_COMPLETED"
                
                # We're in review mode, process as a review command or response
                
                # Check for review-specific commands
                if any(cmd in message_lower for cmd in ['skip', 'next']) and (len(message_lower) <= 5):
                    # Move to next review
                    current_idx = self.current_indices.get(user_id, 0)
                    self.current_indices[user_id] = current_idx + 1
                    return "COMMAND:NEXT_REVIEW"
                
                elif any(cmd in message_lower for cmd in ['summary']):
                    return "COMMAND:SHOW_SUMMARY"
                
                elif any(cmd in message_lower for cmd in ['continue', 'start']) and (len(message_lower) < 10):
                    return "COMMAND:CONTINUE_REVIEWS"
                
                # Number-based response system
                elif "approve" in message_lower:
                    # Option 1: Approve and move to next review
                    reviews = self.review_data[user_id].get('analyzed_reviews', [])
                    current_idx = self.current_indices.get(user_id, 0)
                    
                    if current_idx < len(reviews):
                        reviews[current_idx]['approval_status'] = 'approved'
                        self.last_command[user_id] = 'approve'
                        return "APPROVED:NEXT_REVIEW"
                
                # If last command was feedback request, treat this message as feedback
                elif self.last_command.get(user_id) == 'feedback':
                    reviews = self.review_data[user_id].get('analyzed_reviews', [])
                    current_idx = self.current_indices.get(user_id, 0)
                    
                    if current_idx < len(reviews):
                        reviews[current_idx]['approval_status'] = 'needs_revision'
                        reviews[current_idx]['manager_feedback'] = message_text
                        self.last_command[user_id] = None  # Reset the last command
                        return f"REVISION:{message_text}"
                
                elif any(cmd in message_lower for cmd in ['feedback', 'please']):
                    # Mark that we're expecting feedback next
                    self.last_command[user_id] = 'feedback'
                    return "FEEDBACK_NEEDED:Please provide specific feedback on what changes you'd like to make to the response."
                
                else:
                    # Unclear response, remind of structured interaction options
                    return "UNCLEAR:Oops! I couldn't understand your previous command 🤔\n\nWhile in review mode, please use:\n• 'Approve' to accept\n• 'Feedback' to suggest changes\n• 'Fetch reviews' for new reviews"
            
            # Default to conversation mode if no active review session and no specific command
            return f"CONVERSATION:{message_text}"
        
        return Tool.from_function(
            func=process_whatsapp_message,
            name="ProcessWhatsAppTool",
            description="Processes incoming WhatsApp messages from restaurant managers. Identifies commands, review responses, and conversation context."
        )
    
    def create_review_management_tool(self) -> Tool:
        def manage_review_workflow(action: str, user_id: str, data: str = None) -> str:
            """
            Manage the review approval workflow
            
            Args:
                action: The action to perform (initialize, get_next, revise, summarize, export)
                user_id: The manager's WhatsApp number
                data: Additional data needed for the action (e.g., review JSON or feedback)
                
            Returns:
                str: Result of the action in JSON format
            """
            try:
                result = {}
                
                if action == "initialize":
                    # Parse the review data
                    review_data = json.loads(data)
                    
                    if review_data.get('status') == 'error':
                        return json.dumps({
                            "status": "error",
                            "message": review_data.get('message', 'Unknown error in review data')
                        })
                    
                    # Extract reviews
                    reviews = review_data.get('analyzed_reviews', [])
                    if not reviews:
                        return json.dumps({
                            "status": "error",
                            "message": "No reviews available for processing"
                        })
                    
                    # Store the review data for this manager
                    self.review_data[user_id] = review_data
                    self.current_indices[user_id] = 0
                    self.review_states[user_id] = 'initialized'
                    
                    # Initialize approval status for each review
                    for review in reviews:
                        review['approval_status'] = 'pending'
                    
                    return json.dumps({
                        "status": "success",
                        "message": f"Successfully loaded {len(reviews)} reviews for approval",
                        "review_count": len(reviews)
                    })
                
                elif action == "get_next":
                    if user_id not in self.review_data:
                        return json.dumps({
                            "status": "error",
                            "message": "No active review session"
                        })
                    
                    if self.review_states.get(user_id) == 'completed':
                        return json.dumps({
                            "status": "completed",
                            "message": "All reviews have been processed"
                        })
                    
                    current_idx = self.current_indices.get(user_id, 0)
                    reviews = self.review_data[user_id].get('analyzed_reviews', [])
                    
                    if current_idx >= len(reviews):
                        self.review_states[user_id] = 'completed'
                        return json.dumps({
                            "status": "completed",
                            "message": "All reviews have been processed"
                        })
                    
                    # Get the current review
                    review = reviews[current_idx]
                    
                    return json.dumps({
                        "status": "review",
                        "index": current_idx + 1,
                        "total": len(reviews),
                        "review": review
                    })
                
                elif action == "revise":
                    if user_id not in self.review_data:
                        return json.dumps({
                            "status": "error",
                            "message": "No active review session"
                        })
                    
                    current_idx = self.current_indices.get(user_id, 0)
                    reviews = self.review_data[user_id].get('analyzed_reviews', [])
                    
                    if current_idx >= len(reviews):
                        return json.dumps({
                            "status": "error",
                            "message": "No current review to revise"
                        })
                    
                    # Update the current review with revision data
                    review = reviews[current_idx]
                    review['approval_status'] = 'needs_revision'
                    review['revision_feedback'] = data
                    
                    return json.dumps({
                        "status": "revision_requested",
                        "message": "Response marked for revision",
                        "index": current_idx + 1,
                        "feedback": data
                    })
                
                elif action == "summarize":
                    if user_id not in self.review_data:
                        return json.dumps({
                            "status": "error",
                            "message": "No active review session"
                        })
                    
                    reviews = self.review_data[user_id].get('analyzed_reviews', [])
                    current_idx = self.current_indices.get(user_id, 0)
                    
                    # Count statuses
                    approved = sum(1 for r in reviews if r.get('approval_status') == 'approved')
                    pending = sum(1 for r in reviews if r.get('approval_status') == 'pending')
                    needs_revision = sum(1 for r in reviews if r.get('approval_status') == 'needs_revision')
                    
                    #self.review_states[user_id] = 'completed'

                    return json.dumps({
                        "status": "summary",
                        "total": len(reviews),
                        "approved": approved,
                        "pending": pending,
                        "needs_revision": needs_revision,
                        "current_index": current_idx,
                        "completed": self.review_states.get(user_id) == 'completed'
                    })
                
                elif action == "export":
                    if user_id not in self.review_data:
                        return json.dumps({
                            "status": "error",
                            "message": "No active review session"
                        })
                    
                    # Create a copy of the data with only approved reviews if specified
                    if data == "approved_only":
                        data_copy = self.review_data[user_id].copy()
                        reviews = data_copy.get('analyzed_reviews', [])
                        data_copy['analyzed_reviews'] = [r for r in reviews if r.get('approval_status') == 'approved']
                        return json.dumps(data_copy)
                    else:
                        return json.dumps(self.review_data[user_id])
                
                elif action == "next":
                    if user_id not in self.review_data:
                        return json.dumps({
                            "status": "error",
                            "message": "No active review session"
                        })
                    
                    current_idx = self.current_indices.get(user_id, 0)
                    self.current_indices[user_id] = current_idx + 1
                    
                    return json.dumps({
                        "status": "success",
                        "message": "Moved to next review",
                        "previous_index": current_idx
                    })
                
                else:
                    return json.dumps({
                        "status": "error",
                        "message": f"Unknown action: {action}"
                    })
                
            except Exception as e:
                logger.error(f"Error in review management: {str(e)}")
                return json.dumps({
                    "status": "error",
                    "message": f"Error in review management: {str(e)}"
                })
        
        return Tool.from_function(
            func=manage_review_workflow,
            name="ReviewManagementTool",
            description="Manages the review approval workflow. Actions include initializing reviews, getting the next review, handling revisions, summarizing progress, and exporting results."
        )
    
    def create_memory_management_tool(self) -> Tool:
        def manage_conversation_memory(action: str, user_id: str, message: str = None) -> str:
            """
            Manage conversation memory for users
            
            Args:
                action: The action to perform (save, retrieve, clear)
                user_id: The user's WhatsApp number
                message: The message to save (for 'save' action)
                
            Returns:
                str: Result of the action
            """
            try:
                if action == "save":
                    if not message:
                        return "Error: No message provided to save"
                    
                    memory = self.get_user_memory(user_id)
                    memory.save_context(
                        {"input": message}, 
                        {"output": "Message processed by agent"}
                    )
                    return "Memory saved successfully"
                
                elif action == "retrieve":
                    if user_id not in self.user_memories:
                        return "No memory found for this user"
                    
                    memory = self.user_memories[user_id]
                    return memory.load_memory_variables({})["history"]
                
                elif action == "clear":
                    if user_id in self.user_memories:
                        self.user_memories[user_id].clear()
                    return "Memory cleared successfully"
                
                else:
                    return f"Unknown memory action: {action}"
                
            except Exception as e:
                logger.error(f"Error in memory management: {str(e)}")
                return f"Error managing memory: {str(e)}"
        
        return Tool.from_function(
            func=manage_conversation_memory,
            name="MemoryManagementTool",
            description="Manages conversation memory for users. Allows saving context, retrieving conversation history, and clearing memory."
        )
    def create_crew_output_processor_tool(self) -> Tool:
        """Creates a tool that processes CrewAI outputs into valid JSON data structures."""
        def process_crew_output(crew_output: Any) -> str:
            """
            Process any CrewAI output into valid JSON data.
            
            Args:
                crew_output: The output from a CrewAI run (could be string, CrewOutput object, etc.)
                
            Returns:
                str: A JSON string containing the processed data or an error message
            """
            try:
                # First, convert the output to a string if it's not already
                output_str = str(crew_output) if crew_output is not None else ""
                
                # Log what we're processing for debugging
                logger.info(f"Processing CrewAI output type: {type(crew_output)}")
                logger.info(f"String representation (first 100 chars): {output_str[:100]}")
                
                # Check if the output is empty
                if not output_str or output_str.isspace():
                    logger.warning("Empty or whitespace-only CrewAI output")
                    return json.dumps({
                        "status": "error",
                        "message": "Empty output from CrewAI",
                        "analyzed_reviews": []
                    })
                
                # Try to parse as JSON
                try:
                    json_data = json.loads(output_str)
                    logger.info("Successfully parsed CrewAI output as JSON")
                    return json.dumps(json_data)  # Return a properly formatted JSON string
                except json.JSONDecodeError as e:
                    logger.warning(f"CrewAI output is not valid JSON: {e}")
                    
                    # The output might contain JSON within it - try to extract
                    # Look for patterns like: ```json {...} ```
                    import re
                    json_pattern = r'```(?:json)?\s*({.*?})\s*```'
                    matches = re.findall(json_pattern, output_str, re.DOTALL)
                    
                    if matches:
                        logger.info("Found JSON-like content in code blocks")
                        for potential_json in matches:
                            try:
                                json_data = json.loads(potential_json)
                                logger.info("Successfully extracted JSON from code block")
                                return json.dumps(json_data)
                            except json.JSONDecodeError:
                                continue
                    
                    # If we couldn't extract JSON, create a minimal valid structure with the raw output
                    logger.warning("Creating fallback JSON structure")
                    return json.dumps({
                        "status": "partial",
                        "message": f"Could not parse as JSON: {str(e)}",
                        "raw_output": output_str,
                        "analyzed_reviews": []
                    })
                    
            except Exception as e:
                logger.error(f"Error processing CrewAI output: {str(e)}")
                return json.dumps({
                    "status": "error",
                    "message": f"Error processing output: {str(e)}",
                    "analyzed_reviews": []
                })
    
        return Tool.from_function(
            func=process_crew_output,
            name="CrewOutputProcessorTool",
            description="Processes CrewAI outputs into valid JSON data structures"
        )
    
    def create_template_message_tool(self) -> Tool:
        def send_template_message(to: str, content_sid: str, variables: dict) -> str:
            """
            Send a WhatsApp message using a pre-defined template with buttons
            
            Args:
                to: The recipient's WhatsApp number in format 'whatsapp:+1234567890'
                content_sid: The Twilio Content SID for the template
                variables: Dictionary of variables to populate the template (e.g., {"1": "Customer Name"})
                
            Returns:
                str: Status of the message delivery
            """
            try:
                # Convert variables to proper format if needed
                # if not isinstance(variables, dict):
                #     try:
                #         variables = json.loads(variables)
                #         logger.info(f"Successfully converted variables from string to dict: {variables}")
                #     except Exception as json_err:
                #         error_msg = f"Failed to parse variables as JSON: {str(json_err)}"
                #         logger.error(error_msg)
                #         return error_msg
                
                # Log attempt
                logger.info(f"Sending template message to {to} with content_sid: {content_sid}")
                logger.info(f"Template variables: {variables}")
                
                # Send the template message
                sent_message = self.client.messages.create(
                    content_sid=content_sid,
                    from_=self.twilio_number,
                    to=to,
                    content_variables=json.dumps(variables)
                )
                
                logger.info(f"Sent template message with SID: {sent_message.sid}")
                return f"Template message sent successfully to {to} with SID: {sent_message.sid}"
                
            except Exception as e:
                logger.error(f"Error sending template message: {str(e)}")
                return f"Error sending template message: {str(e)}"
        
        return Tool.from_function(
            func=send_template_message,
            name="SendTemplateMessageTool",
            description="Sends a WhatsApp template message with interactive buttons to a restaurant manager. Used for providing structured options in the conversation."
        )
    
    def create_approval_task(self, manager_phone: str, reviews_json: str = None) -> Task:
        return Task(
            description=f"""
            You are managing the review approval process for a restaurant manager via WhatsApp at {manager_phone}.
            and you have the following reviews to approve:
            {reviews_json}
            
            If this is a new review session:
            1. Initialize the review data using the ReviewManagementTool with action "initialize"
            2. Send the manager a message informing them how many reviews are ready for approval
            3. Ask if they want to start reviewing them now
            
            When the manager responds:
            1. Process their message using the ProcessWhatsAppTool
            2. Based on the response code:
               - If "COMMAND:FETCH_REVIEWS": Tell them you're fetching new reviews (this will be handled externally)
               - If "COMMAND:NEXT_REVIEW" or "COMMAND:CONTINUE_REVIEWS": Get the next review and present it
               - If "COMMAND:SHOW_SUMMARY": Show the review summary
               - If "APPROVED:NEXT_REVIEW": Mark as approved, move to next, and present it
               - If starts with "REVISION:": Use the feedback to improve the response
               - If "UNCLEAR:": Ask for clarification
               - If starts with "CONVERSATION:": Have a normal conversation using your knowledge
               - If "COMMAND:ALL_COMPLETED": Inform them all reviews have been processed and provide options
            
            For presenting reviews:
            1. Format each review clearly showing:
               - Review number and total (e.g., "Review 3 of 10")
               - Customer name
               - Rating (as star emojis)
               - The original review text
               - The suggested response
            2. Ask if the response looks good or needs changes
            
            When handling "COMMAND:ALL_COMPLETED":
            1. Remind the manager that all reviews have been processed
            2. Provide a brief summary of how many were approved/revised
            3. Tell them they can type "summary" to see detailed results
            4. Tell them they can type "fetch reviews" or "get reviews" to check for new reviews
            
            Maintain conversation context:
            1. Save important interactions to memory
            2. Use past context to personalize interactions
            3. Be helpful and professional at all times
            
            When reviews are finished:
            1. Inform the manager all reviews have been processed
            2. Provide a final summary of approved/revised reviews
            3. Ask if they'd like to export the final results
            """,
            expected_output="A detailed report of the review approval process, including how many reviews were approved as-is, how many needed revisions, and what feedback was provided.",
            agent=self.whatsapp_agent,
            async_execution=False
        )
    
    def create_webhook_handler_task(self) -> Task:
        return Task(
            description="""
            You are responsible for handling incoming WhatsApp webhook messages from restaurant managers.
            
            For each incoming message:
            1. Process it using the ProcessWhatsAppTool to determine the intent
            2. Based on the response:
               - For review-related commands, use the ReviewManagementTool
               - For conversation, engage naturally while maintaining context
               - For ambiguous requests, ask clarifying questions
            
            Your response should always be helpful, professional, and tailored to the specific needs of a restaurant manager
            dealing with customer reviews.
            
            Important aspects of your role:
            - Keep track of the review approval state for each manager
            - Remember what review they're currently working on
            - Understand revision requests and help improve responses
            - Make the review approval process as smooth as possible
            """,
            expected_output="Appropriate responses to manager messages that advance the review approval workflow while maintaining a natural conversation flow.",
            agent=self.whatsapp_agent
        )
    
    def create_revise_response_task(self, manager_phone: str, feedback: str) -> Task:
        return Task(
            description=f"""
            A restaurant manager at {manager_phone} has requested revisions to a suggested review response.
            
            Their feedback is: "{feedback}"
            
            Your job is to:
            1. Get the current review using the ReviewManagementTool with action "get_next"
            2. Analyze the manager's feedback carefully
            3. Revise the suggested response to address all their concerns
            4. Make the response more:
               - Personalized to the specific review
               - Empathetic to the customer's experience
               - Professional and representing the restaurant well
               - Addressing specific points mentioned in the review
            5. Send the revised response to the manager for approval
            6. Ask if the new version meets their expectations
            
            Always maintain a professional and helpful tone, and ensure the revised response aligns
            with the restaurant's likely policies and values.
            """,
            expected_output="A significantly improved review response that addresses all the manager's feedback points, sent to them for approval.",
            agent=self.whatsapp_agent
        )