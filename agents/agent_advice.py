from crewai import Agent, Task, LLM
from langchain.tools import Tool
import json
import os
import logging
from typing import Dict, Any, List
from datetime import datetime
from langchain.memory import ConversationBufferMemory

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent_advice")

class AgentAdviceAgent:
    def __init__(self, llm):
        # State tracking
        self.user_memories = {}
        
        # Create the Agent Advice agent
        self.advice_agent = Agent(
            role='Restaurant Industry Expert',
            goal='Provide expert advice and guidance to restaurant managers',
            backstory="""You are a renowned restaurant industry expert with over 20 years of experience 
            in restaurant management, customer service, marketing, and operations. You've helped 
            hundreds of restaurants improve their customer satisfaction, operational efficiency, 
            and profitability. You provide personalized advice to restaurant managers based on 
            industry best practices and the latest trends.""",
            verbose=True,
            allow_delegation=False,
            llm=llm,
            tools=[
                self.create_memory_management_tool()
            ]
        )
    
    def get_user_memory(self, user_id):
        """Retrieve or create a unique memory instance for each user."""
        if user_id not in self.user_memories:
            self.user_memories[user_id] = ConversationBufferMemory(memory_key="history")
        return self.user_memories[user_id]
    
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
    
    def create_advisor_task(self, user_id: str, message: str) -> Task:
        # Get conversation history if available
        history = ""
        try:
            memory = self.get_user_memory(user_id)
            history = memory.load_memory_variables({}).get("history", "")
        except Exception as e:
            logger.error(f"Error loading memory: {str(e)}")
        
        return Task(
            description=f"""
            You are providing expert restaurant advice to a {user_id} who contacted you via WhatsApp.
            
            Their message: "{message}"
            
            Previous conversation history (if any):
            {history}
            
            Your task is to:
            1. Provide helpful, expert advice on restaurant management, operations, customer service, 
               marketing, or any other restaurant-related topic they ask about
            2. Be conversational, friendly, and professional
            3. Draw on your extensive knowledge of restaurant industry best practices
            4. Provide specific, actionable advice that they can implement
            5. If they say "exit" or ask to exit, inform them they're being returned to the main menu
            6. Save important details from their messages to reference in future interactions
            7. if you want to use the memory management tool, use the following format:
                MEMORY:save <user_id> <message>
                MEMORY:retrieve <user_id>
                MEMORY:clear <user_id>
            
            Always maintain a professional tone while being personable and engaging. Your advice should 
            be practical and tailored to a restaurant manager's needs.
            """,
            expected_output="A helpful, expert response to the manager's query about restaurant operations or management",
            agent=self.advice_agent
        )
    
    def handle_advice_request(self, user_id: str, message: str) -> str:
        """Process a message for the advice agent and return a response"""
        # Check for exit command
        if message.lower().strip() == "exit":
            # Clear memory when exiting
            memory = self.get_user_memory(user_id)
            memory.clear()
            
            return "EXIT:You're being returned to the main menu. You can always come back for more advice by selecting 'Agent Advice'."
        
        try:
            # Create the advisor task
            advisor_task = self.create_advisor_task(user_id, message)
            
            # Create a temporary crew to execute the task
            from crewai import Crew
            temp_crew = Crew(
                agents=[self.advice_agent],
                tasks=[advisor_task],
                verbose=True
            )
            
            # Execute the crew to get the response
            crew_output = temp_crew.kickoff()
            
            # Extract the response text
            if hasattr(crew_output, 'raw'):
                response = crew_output.raw
            elif hasattr(crew_output, 'outputs') and len(crew_output.outputs) > 0:
                response = crew_output.outputs[0]
            elif hasattr(crew_output, 'output'):
                response = crew_output.output
            else:
                response = str(crew_output)
            
            # Save the interaction to memory
            memory = self.get_user_memory(user_id)
            memory.save_context(
                {"input": message},
                {"output": response}
            )
            
            return response
        except Exception as e:
            logger.error(f"Error executing advice task: {str(e)}")
            return f"I apologize, but I encountered an error while processing your request. Please try again or type 'exit' to return to the main menu."
    
    def get_welcome_message(self) -> str:
        """Returns the welcome message for the Agent Advice feature"""
        return """
        👨‍🍳 *Restaurant Expert Advisor* 👩‍🍳
        
        Hello! I'm your restaurant industry expert advisor. I can provide guidance on:
        
        • Customer service strategies
        • Staff management and training
        • Menu optimization
        • Marketing and promotion
        • Operational efficiency
        • Industry trends and best practices
        
        Simply ask me any restaurant-related question, and I'll provide expert advice tailored to your needs.
        
        To exit this advisor mode and return to review management, simply type *"exit"*.
        
        How can I help you today?
        """