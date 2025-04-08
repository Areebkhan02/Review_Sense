from crewai import Agent, Task, LLM
from langchain.tools import Tool
import json
import pathlib
import os

class ResponseGeneratorAgent:
    def __init__(self, llm, response_config_path):
        self.llm = llm  # Store the LLM for use in tools
        self.response_config = self.load_response_config(response_config_path)

        # Create the response agent
        self.response_agent = Agent(
            role='Restaurant Response Manager',
            goal='Generate personalized, empathetic responses to customer reviews',
            backstory="""You are an experienced restaurant manager with excellent communication skills.
            You're responsible for crafting personalized responses to customer reviews. You understand 
            the nuances of customer feedback and can address specific concerns with genuine empathy 
            while maintaining the restaurant's reputation. You are skilled at analyzing feedback 
            and creating responses that sound natural and human.""",
            verbose=True,
            allow_delegation=False,
            llm=llm,
            tools=[
                self.create_response_generator_tool()
            ]
        )

    def load_response_config(self, config_path=None):
            """
            Load response configuration from a file.
            
            Args:
                config_path: Path to the configuration file. If None, uses default path.
                
            Returns:
                str: Raw configuration content for response generation or a message indicating no config
            """
            try:
                # Use default path relative to the script location if not provided
                if not config_path:
                    # Get the directory where the script is located
                    base_dir = pathlib.Path(__file__).parent.parent.absolute()
                    config_path = os.path.join(base_dir, "config", "response_templates.txt")
                
                # Ensure the path is resolved correctly
                config_path = os.path.abspath(config_path)
                
                # Check if file exists
                if not os.path.exists(config_path):
                    print(f"Configuration file not found at {config_path}. Using default LLM behavior.")
                    return "NO_CONFIG_FILE_PROVIDED: Using default LLM behavior for response generation."
                
                # Read the configuration file
                with open(config_path, 'r', encoding='utf-8') as file:
                    config_text = file.read()
                
                print(f"Successfully loaded response configuration from {config_path}")
                return config_text
                
            except Exception as e:
                print(f"Error loading configuration file: {str(e)}. Using default LLM behavior.")
                return "ERROR_LOADING_CONFIG: Using default LLM behavior for response generation."
    
        
    def create_response_generator_tool(self) -> Tool:
        def generate_responses(analyzed_reviews_json: str) -> str:
            """
            Generate personalized human-like responses for each review based on deep analysis
            
            Args:
                analyzed_reviews_json: A JSON string with analyzed review data
                
            Returns:
                str: JSON string with reviews including personalized responses
            """
            try:
                # Parse the incoming JSON
                data = json.loads(analyzed_reviews_json)
                
                # Check if we received an error
                if isinstance(data, dict) and data.get('status') == 'error':
                    return analyzed_reviews_json  # Pass through the error
                
                # Get restaurant name and analyzed reviews
                restaurant_name = data.get('restaurant_name', 'our restaurant')
                reviews = data.get('analyzed_reviews', [])
                
                # The response generation will happen within the LLM task execution
                # This tool just prepares and structures the data
                return json.dumps({
                    'status': 'success',
                    'restaurant_name': restaurant_name,
                    'total_analyzed_reviews': len(reviews),
                    'analyzed_reviews': reviews
                })
                
            except json.JSONDecodeError as e:
                print(f"JSONDecodeError: {str(e)}")
                return json.dumps({
                    'status': 'error',
                    'message': f"Error parsing reviews JSON: {str(e)}"
                })
            except Exception as e:
                print(f"Exception in generate_responses: {str(e)}")
                return json.dumps({
                    'status': 'error',
                    'message': f"Error generating responses: {str(e)}"
                })
        
        return Tool.from_function(
            func=generate_responses,
            name="ResponseGeneratorTool",
            description="Prepares review data for LLM-based response generation. Input should be the JSON string from SentimentAnalysisAgent."
        )
    
    def create_response_task(self) -> Task:
        return Task(
            description=f"""
            For each review in the analyzed_reviews array, create a genuine and personalized response
            following both the specific guidelines below AND the response configuration template:
            
            RESPONSE CONFIGURATION TEMPLATE:
            {self.response_config}
            
            Follow these guidelines when generating responses:
            
            1. ANALYZE THE SPECIFIC CONTENT of each review, paying special attention to:
               - The precise issues, complaints, or praise mentioned
               - The emotional tone of the review
               - Any specific dishes, services, or staff mentioned
               - The customer's explicit or implied expectations
               - The summarized_text field which contains key points
            
            2. For each review, compose a COMPLETELY CUSTOM RESPONSE that:
               - Addresses the customer by name
               - Uses appropriate templates from the RESPONSE CONFIGURATION based on star rating
               - References their specific feedback points (not generic)
               - Acknowledges their exact concerns or compliments
               - Provides relevant information or solutions to specific issues
               - Makes the customer feel heard and valued
               - Sounds like it was written by a real person, not an AI
               - Has an appropriate tone based on the review's content and sentiment
               - Varies in structure and wording (avoid repetitive patterns across responses)
               - For negative experiences: Shows sincere concern and offers concrete steps or resolution, invite them to visit again or offer a discount on next visit
               - For positive experiences: Expresses genuine appreciation for specific compliments
            
            3. FORMAT EACH RESPONSE following the template structure:
               - Begin with an appropriate introduction from the template
               - Include 2-3 paragraphs of substance addressing specific points
               - Use a forward-looking closing statement from the template
               - End with the signature format specified in the template
            
            4. ADD each unique, tailored response to its corresponding review under a new field called 'response'
            
            5. Return the complete JSON with all reviews and their new highly-personalized responses
            """,
            expected_output="""A JSON string containing all analyzed reviews, now with highly
            personalized responses that directly address the specific content of each review.
            Each response should follow the configuration template structure while being unique, 
            natural-sounding, and specifically tailored to the individual review's content.""",
            agent=self.response_agent
        )
        
    def create_feedback_revision_task(self, review_text: str, original_response: str, manager_feedback: str) -> Task:
        """Create a task to revise a response based on manager feedback"""
        
        revision_prompt = f"""
        As an experienced restaurant manager, please revise this response to a customer review 
        based on the manager's feedback.
        
        ORIGINAL CUSTOMER REVIEW:
        {review_text}
        
        ORIGINAL RESPONSE:
        {original_response}
        
        MANAGER FEEDBACK:nnn 
        {manager_feedback}
        
        Your job is to create a revised response that:
        - Specifically addresses all points raised in the manager's feedback
        - Maintains a professional, warm, and personalized tone
        - Sounds natural and human-written (not AI-generated)
        - Is specific to the original review's content
        - Avoids overusing phrases like "I understand" or "I apologize"
        - Is concise yet comprehensive
        
        Return only the revised response without any explanations or additional text
        Dont use any tools available to you just return the response.
        """
        
        return Task(
            description=revision_prompt,
            expected_output="A revised, personalized response that incorporates the manager's feedback.",
            agent=self.response_agent,
            tools=[] 
        )