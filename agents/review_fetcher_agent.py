from crewai import Agent, Task, LLM
from langchain.tools import Tool
import os
import json
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time

class ReviewFetcherAgent:
    def __init__(self, llm):
        self.fetcher_agent = Agent(
            role='Review Fetching Specialist',
            goal='Fetch and filter recent restaurant reviews, focusing on those requiring attention',
            backstory="""You are a specialized agent responsible for monitoring and 
            collecting restaurant reviews. You have extensive experience in identifying 
            reviews that need immediate attention, particularly those with lower ratings 
            or specific customer concerns.""",
            verbose=True,
            allow_delegation=False,
            llm=llm,
            tools=[
                self.create_review_fetch_tool(),
                self.create_review_filter_tool()
            ]
        )

    def create_review_fetch_tool(self) -> Tool:
        def fetch_reviews(restaurant_name: str, num_reviews: int = 10) -> str:
            """
            Fetch reviews for a restaurant from Google Maps
            
            Args:
                restaurant_name (str): Name of the restaurant to search for
                num_reviews (int): Number of reviews to fetch (default: 10)
                
            Returns:
                str: JSON string containing scraped reviews
            """
            # Initialize seen_reviews set and reviews list at the start of the function
            seen_reviews = set()
            reviews = []
            
            # Set up Chrome options for headless browsing
            print(f"Setting up Chrome options for {restaurant_name}")
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")
            #addition for production environment
            chrome_options.binary_location = "/usr/bin/chromium-browser"
            
            driver = None
            print(f"Initializing Chrome driver")
            try:
                # CHANGED: Skip WebDriverManager and use system ChromeDriver directly
                print("Using system ChromeDriver instead of WebDriverManager")
                
                # Debug information
                import subprocess
                try:
                    print("Checking Chromium installation:")
                    result = subprocess.run(["ls", "-la", "/usr/bin/chromium*"], shell=True, capture_output=True, text=True)
                    print("Chromium installation result: ", result.stdout)
                    
                    print("Checking ChromeDriver installation:")
                    result = subprocess.run(["ls", "-la", "/usr/bin/chromedriver*"], shell=True, capture_output=True, text=True)
                    print("ChromeDriver installation result: ", result.stdout)
                except Exception as e:
                    print(f"Checking installations failed: {e}")
                
                # Try direct path to ChromeDriver
                service = Service("/usr/bin/chromedriver")
                driver = webdriver.Chrome(service=service, options=chrome_options)
                
                # Search for the restaurant on Google Maps directly
                print(f"Searching for: {restaurant_name}")
                search_url = f"https://www.google.com/maps/search/{restaurant_name.replace(' ', '+')}"
                print(f"Searching for: {search_url}")
                driver.get(search_url)
                time.sleep(3)  # Wait for page to load
                
                # Find and click on the first result
                try:
                    first_result = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "div.V0h1Ob-haAclf, div.Nv2PK"))
                    )
                    first_result.click()
                    print("Clicked on first restaurant result")
                    time.sleep(2)  # Wait for the restaurant page to load
                    
                    # Find and click on reviews
                    reviews_section = None
                    try:
                        # Try the first selector pattern
                        reviews_section = WebDriverWait(driver, 5).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label*='review' i]"))
                        )
                    except:
                        # Try alternative selector patterns
                        try:
                            reviews_section = WebDriverWait(driver, 5).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-tab-index='0'][aria-selected='false']"))
                            )
                        except:
                            reviews_section = WebDriverWait(driver, 5).until(
                                EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'review')]"))
                            )
                    
                    reviews_section.click()
                    print("Clicked on reviews section")
                    time.sleep(3)  # Wait for reviews to load
                    
                    # Scroll to load more reviews
                    print("Scrolling to load more reviews...")
                    last_review_count = 0
                    scroll_attempts = 0
                    max_scroll_attempts = 15

                    # Find the scrollable container
                    scrollable_div = None
                    try:
                        # This is the most reliable selector for the reviews container in Google Maps
                        scrollable_div = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='feed']"))
                        )
                        print(f"Found reviews container")
                    except Exception as e:
                        print(f"Could not find specific reviews container: {str(e)}")
                        # Fallback options
                        for selector in ["div.m6QErb", "div.m6QErb.DxyBCb.kA9KIf.dS8AEf", ".section-scrollbox"]:
                            try:
                                scrollable_div = WebDriverWait(driver, 3).until(
                                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                                )
                                print(f"Found fallback scrollable container with selector: {selector}")
                                break
                            except:
                                continue

                    if not scrollable_div:
                        print("Using body as scrollable element")
                        scrollable_div = driver.find_element(By.TAG_NAME, "body")

                    while True:
                        # Get current number of reviews
                        review_elements = driver.find_elements(By.CSS_SELECTOR, "div.jftiEf, div.jJc9Ad")
                        current_review_count = len(review_elements)
                        
                        print(f"Currently found {current_review_count} reviews, need {num_reviews}")
                        
                        # Break if we have enough reviews or if no new reviews were loaded after multiple attempts
                        if current_review_count >= num_reviews:
                            print(f"Found {current_review_count} reviews, which meets our target of {num_reviews}")
                            break
                            
                        if current_review_count == last_review_count:
                            scroll_attempts += 1
                            print(f"No new reviews found after scroll attempt {scroll_attempts}/{max_scroll_attempts}")
                            if scroll_attempts >= max_scroll_attempts:
                                print(f"Reached maximum scroll attempts, stopping with {current_review_count} reviews")
                                break
                        else:
                            scroll_attempts = 0  # Reset counter if we got new reviews
                            print(f"Found {current_review_count - last_review_count} new reviews")
                        
                        # Update last_review_count
                        last_review_count = current_review_count
                        
                        # Try multiple scrolling techniques
                        try:
                            # Method 1: More reliable pixel-based scrolling
                            driver.execute_script(
                                "arguments[0].scrollBy(0, 300);", scrollable_div
                            )
                            
                            # Method 2: Find the last review and scroll to it
                            if current_review_count > 0:
                                last_review = review_elements[-1]
                                driver.execute_script("arguments[0].scrollIntoView(true);", last_review)
                            
                            # Method 3: Try to click "More reviews" button if it exists
                            try:
                                more_buttons = driver.find_elements(By.XPATH, "//*[contains(text(), 'More reviews') or contains(text(), 'more review')]")
                                if more_buttons:
                                    driver.execute_script("arguments[0].click();", more_buttons[0])
                                    print("Clicked 'More reviews' button")
                            except Exception as scroll_e:
                                print(f"No 'More reviews' button found: {str(scroll_e)}")
                                
                            # Wait longer for reviews to load (Google Maps can be slow)
                            time.sleep(3)
                            
                        except Exception as e:
                            print(f"Error during scrolling: {str(e)}")
                            scroll_attempts += 1
                    
                    # Wait for reviews to be present
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.jftiEf, div.jJc9Ad"))
                    )
                    review_elements = driver.find_elements(By.CSS_SELECTOR, "div.jftiEf, div.jJc9Ad")
                    
                    print(f"Found {len(review_elements)} review elements")
                    for i, element in enumerate(review_elements[:num_reviews]):  # Get up to specified number of reviews
                        if i >= num_reviews:
                            break
                        
                        try:
                            print(f"Processing review {i+1}/{num_reviews}")
                            # Extract rating (1-5 stars)
                            try:
                                rating_element = element.find_element(By.CSS_SELECTOR, "span.kvMYJc, span[role='img']")
                                rating_text = rating_element.get_attribute("aria-label")
                                rating = int(rating_text.split()[0]) if rating_text else None
                            except Exception as e:
                                print(f"Error extracting rating: {str(e)}")
                                rating = None
                            
                            # Try to expand the review text if it's collapsed
                            try:
                                more_button = element.find_element(By.CSS_SELECTOR, "button.w8nwRe.kyuRq, button.w8nwRe")
                                driver.execute_script("arguments[0].click();", more_button)
                                print("Expanded review text")
                                time.sleep(0.5)
                            except:
                                pass  # No "More" button, text already fully visible
                            
                            # Extract review text
                            try:
                                # Try multiple possible selectors for review text
                                text = ""
                                possible_selectors = [
                                    "span.wiI7pd",  # Original selector
                                    "div.MyEned",   # Alternative selector
                                    "div.review-full-text",  # New possible selector
                                    "span[jsan*='7.wiI7pd']",  # More specific selector
                                    ".review-content-with-avatar div[data-expandable-section]"  # Another alternative
                                ]
                                
                                for selector in possible_selectors:
                                    try:
                                        text_element = element.find_element(By.CSS_SELECTOR, selector)
                                        text = text_element.text
                                        if text:
                                            break
                                    except:
                                        continue
                                
                                if not text:
                                    # Fallback: try to find any div containing review-like text
                                    text_element = element.find_element(By.XPATH, ".//div[string-length(text()) > 20]")
                                    text = text_element.text
                            except Exception as e:
                                print(f"Error extracting text: {str(e)}")
                                text = ""
                            
                            # Extract time posted
                            try:
                                time_element = element.find_element(By.CSS_SELECTOR, "span.rsqaWe, div.DU9Pgb")
                                time_posted = time_element.text
                            except Exception as e:
                                print(f"Error extracting time: {str(e)}")
                                time_posted = ""
                            
                            # Extract author name
                            try:
                                author_element = element.find_element(By.CSS_SELECTOR, "div.d4r55, div.TSUbDb")
                                author = author_element.text
                            except Exception as e:
                                print(f"Error extracting author: {str(e)}")
                                author = ""
                            
                            # Create a unique identifier for this review
                            review_id = f"{author}:{text[:50]}"
                            
                            # Only add if we haven't seen this review before
                            if review_id not in seen_reviews:
                                reviews.append({
                                    'rating': rating,
                                    'text': text,
                                    'time': time_posted,
                                    'author': author
                                })
                                seen_reviews.add(review_id)
                                print(f"Successfully extracted review {i+1}")
                            else:
                                print(f"Skipping duplicate review from {author}")
                            
                        except Exception as e:
                            print(f"Error processing review {i+1}: {str(e)}")
                            continue
                    
                    if not reviews:
                        return json.dumps({
                            'status': 'error', 
                            'message': "No reviews found or could not extract reviews",
                            'restaurant_name': restaurant_name,
                            'reviews': []
                        })
                    
                    return json.dumps({
                        'status': 'success',
                        'restaurant_name': restaurant_name,
                        'total_reviews': len(reviews),
                        'reviews': reviews
                    })
                        
                except Exception as e:
                    print(f"Error finding or processing reviews: {str(e)}")
                    return json.dumps({
                        'status': 'error',
                        'message': f"Error finding or processing reviews: {str(e)}",
                        'restaurant_name': restaurant_name,
                        'reviews': []
                    })
                    
            except Exception as e:
                print(f"Error initializing scraper: {str(e)}")
                return json.dumps({
                    'status': 'error',
                    'message': f"Error initializing scraper: {str(e)}",
                    'restaurant_name': restaurant_name,
                    'reviews': []
                })
            
            finally:
                if driver:
                    driver.quit()
                    print("WebDriver closed")

        return Tool.from_function(
            func=fetch_reviews,
            name="ReviewFetchTool",
            description="Fetches restaurant reviews from Google Maps. Returns a JSON string with review data."
        )

    def create_review_filter_tool(self) -> Tool:
        def filter_reviews(reviews_json: str) -> str:
            """
            Filter reviews to only include those with 5 stars or less
            
            Args:
                reviews_json: A JSON string with review data from the fetch tool
                
            Returns:
                str: JSON string with only reviews that have 5 stars or less
            """
            try:
                # Try to parse the JSON
                print(f"Received JSON for filtering: {reviews_json[:100]}...")
                
                # Handle the input whether it's direct JSON or nested in a dict
                try:
                    data = json.loads(reviews_json)
                except json.JSONDecodeError as e:
                    print(f"JSONDecodeError: {str(e)}")
                    return json.dumps({
                        'status': 'error',
                        'message': f"Error parsing reviews JSON: {str(e)}"
                    })
                
                # Check if we received an error
                if isinstance(data, dict) and data.get('status') == 'error':
                    return reviews_json  # Pass through the error
                    
                # Get the reviews array
                if 'reviews' not in data:
                    return json.dumps({
                        'status': 'error',
                        'message': "No 'reviews' field found in JSON data"
                    })
                    
                reviews = data['reviews']
                restaurant_name = data.get('restaurant_name', 'Unknown Restaurant')
                
                # Filter reviews that are 3 stars or less
                filtered_reviews = [
                    review for review in reviews
                    if review.get('rating') is not None and review.get('rating') <= 5
                ]
                
                # Return only the filtered reviews without any summary
                return json.dumps({
                    'status': 'success',
                    'restaurant_name': restaurant_name,
                    'total_filtered_reviews': len(filtered_reviews),
                    'filtered_reviews': filtered_reviews
                })

            except Exception as e:
                print(f"Exception in filter_reviews: {str(e)}")
                return json.dumps({
                    'status': 'error',
                    'message': f"Error filtering reviews: {str(e)}"
                })

        return Tool.from_function(
            func=filter_reviews,
            name="ReviewFilterTool",
            description="Filters reviews to only include those with 5 stars or less. Input should be the JSON string from ReviewFetchTool."
        )

    def create_fetch_task(self, restaurant_name: str, num_reviews: int) -> Task:
        print(f"Creating fetch task for {restaurant_name} with {num_reviews} reviews")
        return Task(
            description=f"""
            1. Fetch {num_reviews} recent reviews for {restaurant_name}
            2. Filter reviews to identify those that:
               - Have 5 stars or less
            3. Organize the filtered reviews by priority
            4. Prepare a summary of reviews requiring attention
            """,
            expected_output="""A JSON string containing filtered reviews with:
            - Restaurant name
            - Total number of filtered reviews
            - List of filtered reviews (5 stars or less)
            Each review should include rating, text, time posted, and author.""",
            agent=self.fetcher_agent
        )