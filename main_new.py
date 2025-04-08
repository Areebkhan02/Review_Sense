from crewai import Crew, LLM
import os
from agents.review_fetcher_agent import ReviewFetcherAgent
from agents.sentiment_analysis_agent import SentimentAnalysisAgent
from agents.response_generator_agent import ResponseGeneratorAgent
from agents.whatsapp_agent import WhatsAppAgent
from dotenv import load_dotenv

# Set up Gemini API key


# Load environment variables
load_dotenv()


# Initialize the LLM
my_llm = LLM(
    api_key=os.environ["GOOGLE_API_KEY"],
    model="gemini/gemini-2.0-flash",
)

response_config_path = os.environ.get("RESPONSE_CONFIG_PATH")



def run_review_workflow(restaurant_name: str = "lalqila", num_reviews: int = 10):
    """Run the review analysis workflow and return the results"""
    # Create the agents
    fetcher_system = ReviewFetcherAgent(my_llm)
    analysis_system = SentimentAnalysisAgent(my_llm)
    response_system = ResponseGeneratorAgent(my_llm, response_config_path)
    
    # Create the tasks
    fetcher_task = fetcher_system.create_fetch_task(restaurant_name, num_reviews)
    analysis_task = analysis_system.create_analysis_task()
    response_task = response_system.create_response_task()
    
    # Create a crew with all agents and sequential tasks
    crew = Crew(
        agents=[
            fetcher_system.fetcher_agent, 
            analysis_system.analysis_agent,
            response_system.response_agent
        ],
        tasks=[
            fetcher_task, 
            analysis_task,
            response_task
        ],
        verbose=True
    )

    # Execute the complete workflow
    result = crew.kickoff()
    return result

def run_approval_workflow(manager_phone: str, reviews_json: str):
    """Run the approval workflow with a WhatsApp agent"""
    # Create the WhatsApp agent
    whatsapp_system = WhatsAppAgent(my_llm)
    
    # Create approval task
    approval_task = whatsapp_system.create_approval_task(manager_phone, reviews_json)
    
    # Create a crew for the WhatsApp approval workflow
    approval_crew = Crew(
        agents=[whatsapp_system.whatsapp_agent],
        tasks=[approval_task],
        verbose=True
    )
    
    # Execute the WhatsApp approval workflow
    result = approval_crew.kickoff()
    return result

def main(restaurant_name: str = "lalqila", num_reviews: int = 10, manager_phone: str = None):
    """Run the full workflow with optional WhatsApp approval"""
    # Run the review analysis workflow
    review_result = run_review_workflow(restaurant_name, num_reviews)
    
    # If manager_phone is provided, run the approval workflow
    if manager_phone:
        final_result = run_approval_workflow(manager_phone, review_result)
        return final_result
    
    return review_result

if __name__ == "__main__":
    # Example usage with manager's phone number in WhatsApp format
    main("lalqila", 40, "whatsapp:+923341336686")