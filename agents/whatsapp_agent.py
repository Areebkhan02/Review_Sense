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
        #self.user_memories = {}
        #self.last_command = {}
        self.last_activity = {}
        
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
                self.create_llm_intent_processor_tool(),
                self.create_review_management_tool(),
                self.create_crew_output_processor_tool(),
                self.create_template_message_tool()
            ]
        )
    
    # Reset review-related state variables for a specific user after review completion. 
    # This is used to clear the state when the user has completed all reviews.
    def reset_user_review_state(self, user_id: str) -> None:
        """
        Reset review-related state variables for a specific user after review completion.
        
        Args:
            user_id: The user's WhatsApp number to clear state for
        """
        logger.info(f"Resetting review state for user: {user_id}")
        
        # Clear review session data
        if user_id in self.review_data:
            del self.review_data[user_id]
        
        if user_id in self.current_indices:
            del self.current_indices[user_id]
            
        if user_id in self.review_states:
            del self.review_states[user_id]
            
        
        # We maintain last_activity and user_memories for continuity
        logger.info(f"Successfully reset review state for user: {user_id}")
    
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
                if action == "summarize":
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
    
    
    def create_intent_classification_task(self, message_text: str) -> Task:
        """Creates a task to classify the intent of a message"""
        return Task(
            description=f"""
            As a message intent classifier, determine the intent of this message from a restaurant manager reviewing customer response drafts.
            
            Message: "{message_text}"
            
            Analyze the message carefully and classify it into EXACTLY ONE of these categories:
            1. APPROVED - If the message indicates approval (examples: "looks good", "approve", "yes", "good", "👍", "this is good", "perfect", "send it")
            2. REVISION - If the message suggests changes or provides feedback (examples: "add a discount", "change this", "offer something", "not good", "revise", "fix this")
            3. UNCLEAR - If the message is ambiguous or unrelated (examples: "hmm", "maybe", "i don't know", "what do you think?")
            
            Return ONLY the intent identifier (APPROVED, REVISION, or UNCLEAR) without any explanation or additional text.
            """,
            expected_output="A single word representing the message intent: APPROVED, REVISION, or UNCLEAR",
            agent=self.whatsapp_agent
        )

    def create_llm_intent_processor_tool(self) -> Tool:
        """Creates a tool that uses LLM to process message intent."""
        def process_message_intent(user_id: str, message_text: str) -> str:
            """
            Process an incoming message using LLM to determine intent
            
            Args:
                user_id: The user's WhatsApp number
                message_text: The message content
                
            Returns:
                str: The determined intent command
            """
            # First check if we're in an active review session
            if user_id not in self.review_data:
                # If no active review session, treat as conversation
                return "CONVERSATION:WELCOME"
            
            # If we're in a review session
            current_idx = self.current_indices.get(user_id, 0)
            reviews = self.review_data.get(user_id, {}).get('analyzed_reviews', [])
            
            # If all reviews completed
            if current_idx >= len(reviews) or self.review_states.get(user_id) == 'completed':
                return "COMMAND:ALL_COMPLETED"
            
            # Create a temporary crew to run the intent classification task
            intent_task = self.create_intent_classification_task(message_text)
            
            try:
                from crewai import Crew
                
                # Create a small crew just for intent classification
                intent_crew = Crew(
                    agents=[self.whatsapp_agent],
                    tasks=[intent_task],
                    verbose=False
                )
                
                # Execute the task
                intent_result = intent_crew.kickoff()
                
                # Extract the intent from the result
                if hasattr(intent_result, 'raw'):
                    intent = intent_result.raw.strip().upper()
                else:
                    intent = str(intent_result).strip().upper()
                
                logger.info(f"Classified message intent: {intent}")
                
                # Map the intent to command format
                if "APPROVED" in intent:
                    # Mark the current review as approved
                    reviews[current_idx]['approval_status'] = 'approved'
                    return "APPROVED:NEXT_REVIEW"
                elif "REVISION" in intent:
                    # Mark that the review needs revision
                    reviews[current_idx]['approval_status'] = 'needs_revision'
                    reviews[current_idx]['manager_feedback'] = message_text
                    return f"REVISION:{message_text}"
                else:
                    return "UNCLEAR:Intent not clear"
                
            except Exception as e:
                logger.error(f"Error in LLM intent classification: {str(e)}")
                # Fall back to looking for keywords
                message_lower = message_text.lower()
                
                # Simple keyword matching as fallback
                if any(approval in message_lower for approval in ['approve', 'good', 'yes', 'ok', 'send']):
                    reviews[current_idx]['approval_status'] = 'approved'
                    return "APPROVED:NEXT_REVIEW"
                elif any(revision in message_lower for revision in ['revise', 'change', 'edit', 'discount', 'offer']):
                    reviews[current_idx]['approval_status'] = 'needs_revision'
                    reviews[current_idx]['manager_feedback'] = message_text
                    return f"REVISION:{message_text}"
                else:
                    return "UNCLEAR:Intent not clear"
        
        return Tool.from_function(
            func=process_message_intent,
            name="LLMIntentProcessorTool",
            description="Processes message intent using LLM to determine if it's approval, revision request, or unclear."
        )