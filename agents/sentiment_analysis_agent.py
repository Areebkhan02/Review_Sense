from crewai import Agent, Task, LLM
from langchain.tools import Tool
import json
from datetime import datetime, timedelta

class SentimentAnalysisAgent:
    def __init__(self, llm):
        self.analysis_agent = Agent(
            role='Review Sentiment Analyst',
            goal='Analyze restaurant reviews and extract key information with sentiment analysis',
            backstory="""You are an expert sentiment analyst specializing in restaurant reviews. 
            You can identify underlying customer emotions, key issues, and summarize lengthy 
            feedback into concise, actionable insights for restaurant management.""",
            verbose=True,
            allow_delegation=False,
            llm=llm,
            tools=[self.create_sentiment_analysis_tool()]
        )
        
    def create_sentiment_analysis_tool(self) -> Tool:
        def analyze_sentiment(reviews_json: str) -> str:
            """
            Analyze the sentiment of filtered reviews and prepare for summarization
            
            Args:
                reviews_json: A JSON string with filtered review data
                
            Returns:
                str: JSON string with reviews prepared for summarization
            """
            try:
                # Parse the incoming JSON
                data = json.loads(reviews_json)
                
                # Check if we received an error
                if isinstance(data, dict) and data.get('status') == 'error':
                    return reviews_json  # Pass through the error
                
                # Get filtered reviews
                if 'filtered_reviews' not in data:
                    return json.dumps({
                        'status': 'error',
                        'message': "No 'filtered_reviews' field found in JSON data"
                    })
                
                reviews = data['filtered_reviews']
                restaurant_name = data.get('restaurant_name', 'Unknown Restaurant')
                
                # Process each review for sentiment analysis
                analyzed_reviews = []
                for review in reviews:
                    # Extract existing data
                    rating = review.get('rating')
                    text = review.get('text', '')
                    author = review.get('author', '')
                    time_raw = review.get('time', '')
                    
                    # Format the time to Month-Year format
                    formatted_time = time_raw
                    try:
                        # Clean up the time string - remove Unicode stars and extra newlines
                        clean_time = time_raw
                        if '\ue838' in clean_time:
                            # Extract only the actual time information (at the end after stars)
                            parts = clean_time.split('\n')
                            # Find the part that contains time information
                            for part in parts:
                                if any(time_indicator in part for time_indicator in ['day', 'week', 'month', 'year']):
                                    clean_time = part.strip()
                                    break
                        
                        # Common time formats in Google reviews
                        if "week" in clean_time or "day" in clean_time or "month" in clean_time or "year" in clean_time:
                            current_date = datetime.now()
                            
                            if "day" in clean_time:
                                # Extract the number before "day"
                                days_text = clean_time.split("day")[0].strip()
                                days = int(''.join(filter(str.isdigit, days_text)) or "1")
                                review_date = current_date - timedelta(days=days)
                            elif "week" in clean_time:
                                # Extract the number before "week"
                                weeks_text = clean_time.split("week")[0].strip()
                                weeks = int(''.join(filter(str.isdigit, weeks_text)) or "1")
                                review_date = current_date - timedelta(weeks=weeks)
                            elif "month" in clean_time:
                                # Extract the number before "month"
                                months_text = clean_time.split("month")[0].strip()
                                months = int(''.join(filter(str.isdigit, months_text)) or "1")
                                # Approximate month as 30 days
                                review_date = current_date - timedelta(days=months*30)
                            elif "year" in clean_time:
                                # Extract the number before "year"
                                years_text = clean_time.split("year")[0].strip()
                                years = int(''.join(filter(str.isdigit, years_text)) or "1")
                                # Approximate year as 365 days
                                review_date = current_date - timedelta(days=years*365)
                            
                            # Format as Month-Year
                            formatted_time = review_date.strftime("%B-%Y")
                        else:
                            formatted_time = clean_time
                    except Exception as e:
                        print(f"Error formatting time: {str(e)}")
                        formatted_time = "Unknown Date"
                    
                    # Determine sentiment based on rating (we'll need this for the summarization prompts)
                    if rating <= 2:
                        sentiment = "negative"
                    elif rating == 3:
                        sentiment = "neutral"
                    else:
                        sentiment = "positive"
                    
                    # Create the analyzed review structure
                    # Note that we're leaving summarized_text empty for the agent to fill in
                    analyzed_reviews.append({
                        'rating': rating,
                        'text': text,
                        'sentiment': sentiment,  # Include sentiment classification
                        'time': formatted_time,
                        'author': author
                    })
                
                # Return the analyzed reviews ready for summarization
                return json.dumps({
                    'status': 'success',
                    'restaurant_name': restaurant_name,
                    'total_analyzed_reviews': len(analyzed_reviews),
                    'analyzed_reviews': analyzed_reviews
                })
                
            except json.JSONDecodeError as e:
                print(f"JSONDecodeError: {str(e)}")
                return json.dumps({
                    'status': 'error',
                    'message': f"Error parsing reviews JSON: {str(e)}"
                })
            except Exception as e:
                print(f"Exception in analyze_sentiment: {str(e)}")
                return json.dumps({
                    'status': 'error',
                    'message': f"Error analyzing reviews: {str(e)}"
                })
        
        return Tool.from_function(
            func=analyze_sentiment,
            name="SentimentAnalysisTool",
            description="Analyzes the sentiment of restaurant reviews and formats times. Input should be the JSON string from ReviewFilterTool."
        )
    
    def create_analysis_task(self) -> Task:
        return Task(
            description="""
            1. Take the filtered reviews from the ReviewFetcherAgent.
            2. Use the SentimentAnalysisTool to process the reviews.
            3. Once you have the processed reviews, for EACH review in the 'analyzed_reviews' list:
               - Read the full review text
               - Note the sentiment classification (positive, neutral, negative)
               - Generate a concise two-line summary that captures the key points and sentiment
               - Make sure each summary is informative and highlights the most important aspects
            4. Create a final JSON output that includes all the fields from the SentimentAnalysisTool output,
               but add your generated 'summarized_text' for each review.
            5. The final output should maintain the same structure with 'restaurant_name', 
               'total_analyzed_reviews', and 'analyzed_reviews' array, but each review should now 
               include a 'summarized_text' field with your LLM-generated summary.
            """,
            expected_output="""A JSON string containing analyzed reviews with:
            - Restaurant name
            - Total number of analyzed reviews
            - List of analyzed reviews, each containing:
              * rating
              * text (original review)
              * summarized_text (your 2-line summary)
              * time (in Month-Year format)
              * author""",
            agent=self.analysis_agent
        )