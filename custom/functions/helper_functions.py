"""
This file contains helper functions that are used in the review sense project.
"""


def filter_reviews_by_rating(review_data, max_rating=3):
    """
    Filter reviews to keep only those with rating less than or equal to max_rating.
    
    Args:
        review_data (dict): The JSON data containing reviews
        max_rating (int): Maximum rating to keep (default: 3)
        
    Returns:
        tuple: (filtered_data, removed_count)
            - filtered_data: JSON with filtered reviews
            - removed_count: Number of reviews that were removed
    """
    if not review_data or 'analyzed_reviews' not in review_data:
        return review_data, 0
    
    # Get original reviews and count
    original_reviews = review_data['analyzed_reviews']
    original_count = len(original_reviews)
    
    # Filter reviews to keep only those with rating ≤ max_rating
    filtered_reviews = [review for review in original_reviews if int(review.get('rating', 0)) <= max_rating]
    
    # Calculate how many were removed
    removed_count = original_count - len(filtered_reviews)
    
    # Create a new data structure with the filtered reviews
    filtered_data = review_data.copy()
    filtered_data['analyzed_reviews'] = filtered_reviews
    
    # Update the total_analyzed_reviews field if it exists
    if 'total_analyzed_reviews' in filtered_data:
        filtered_data['total_analyzed_reviews'] = len(filtered_reviews)
    
    return filtered_data, removed_count


