from pymongo import MongoClient
import os
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables
load_dotenv()

class MongoDB:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MongoDB, cls).__new__(cls)
            # Get MongoDB connection string from environment variables
            mongodb_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
            db_name = os.getenv("MONGODB_DB_NAME", "review_sense")
            
            # Create MongoDB client
            cls._instance.client = MongoClient(mongodb_uri)
            cls._instance.db = cls._instance.client[db_name]
            
            # Initialize collections
            cls._instance.restaurants = cls._instance.db.restaurants
            cls._instance.reviews = cls._instance.db.reviews
            
            # Create index for faster queries
            cls._instance.reviews.create_index("restaurant_id")
            
        return cls._instance
    
    def save_restaurant(self, restaurant_name):
        """Save or get restaurant ID"""
        result = self.restaurants.update_one(
            {"name": restaurant_name},
            {"$set": {"name": restaurant_name}},
            upsert=True
        )
        
        # Return the restaurant ID
        if result.upserted_id:
            return result.upserted_id
        else:
            restaurant = self.restaurants.find_one({"name": restaurant_name})
            return restaurant["_id"]
    
    def save_reviews(self, restaurant_name, analyzed_reviews):
        """Save reviews from the review workflow"""
        # Get or create restaurant
        restaurant_id = self.save_restaurant(restaurant_name)
        
        # Insert or update each review
        saved_count = 0
        for review in analyzed_reviews:
            # Create a unique identifier for the review
            review_identifier = f"{review['author']}:{review['text'][:50]}"
            
            # Prepare review data
            review_data = {
                "restaurant_id": restaurant_id,
                "identifier": review_identifier,
                "rating": review.get("rating"),
                "text": review.get("text", ""),
                "author": review.get("author", ""),
                "time": review.get("time", ""),
                "sentiment": review.get("sentiment", "")
            }
            
            # Add response if it exists
            if "response" in review:
                review_data["response"] = review["response"]
            
            # Update or insert the review
            self.reviews.update_one(
                {
                    "restaurant_id": restaurant_id,
                    "identifier": review_identifier
                },
                {"$set": review_data},
                upsert=True
            )
            saved_count += 1
        
        return {"saved_reviews": saved_count}
    
    def update_review_response(self, restaurant_name, author, text_prefix, response):
        """Update a review's response using identifiers"""
        # Get restaurant
        restaurant = self.restaurants.find_one({"name": restaurant_name})
        if not restaurant:
            return False
        
        # Create the identifier
        review_identifier = f"{author}:{text_prefix[:50]}"
        
        # Update the response
        result = self.reviews.update_one(
            {
                "restaurant_id": restaurant["_id"],
                "identifier": review_identifier
            },
            {"$set": {"response": response}}
        )
        
        return result.modified_count > 0
    
    def get_reviews_for_restaurant(self, restaurant_name):
        """Get all reviews for a specific restaurant"""
        restaurant = self.restaurants.find_one({"name": restaurant_name})
        if not restaurant:
            return []
            
        return list(self.reviews.find({"restaurant_id": restaurant["_id"]}))