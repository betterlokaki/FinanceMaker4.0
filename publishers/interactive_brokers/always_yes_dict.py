"""Dictionary that automatically answers 'yes' to all IBKR questions."""
from typing import Any


class AlwaysYesDict(dict):
    """Dictionary that always returns True for any missing key.
    
    This ensures all IBKR questions are automatically answered "yes",
    even if the specific question text isn't in the dictionary.
    
    Overrides __missing__, __contains__, and get() to handle all
    dictionary access patterns that ibind's find_answer might use.
    When a key is accessed, it's automatically stored in the dict
    so subsequent checks will find it.
    """
    
    def __missing__(self, key: Any) -> bool:
        """Return True for any missing key and store it.
        
        Args:
            key: The question key (QuestionType enum or string).
            
        Returns:
            Always returns True to auto-confirm all questions.
        """
        # Store the key so subsequent checks will find it
        self[key] = True
        return True
    
    def __contains__(self, key: Any) -> bool:
        """Return True if key exists or always return True for missing keys.
        
        This handles cases where ibind checks 'key in dict' before accessing.
        
        Args:
            key: The question key (QuestionType enum or string).
            
        Returns:
            True if key exists in underlying dict, or True for any key.
        """
        # Check if key exists in parent dict first
        if super().__contains__(key):
            return True
        # For missing keys, return True and store it
        self[key] = True
        return True
    
    def get(self, key: Any, default: Any = None) -> bool:
        """Return True for any key, storing it if missing.
        
        This handles cases where ibind uses dict.get(key).
        
        Args:
            key: The question key (QuestionType enum or string).
            default: Ignored, always returns True.
            
        Returns:
            Always returns True.
        """
        if key not in super():
            self[key] = True
        return True
