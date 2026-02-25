import time
from google import genai
from google.genai import types
from os import getenv
client = genai.Client(api_key=getenv("GEMINI_API_KEY"))

# 1. Create a persistent "Search Store" 
# This is like your private database/server for the paper.
# print("Creating persistent store...")
# store = client.file_search_stores.create(
#     config={'display_name': 'Earning Stocks Prediction Research Library'}
# )

# 2. Upload the DOCX to the store
# Note: We use a simple dictionary for 'config' to avoid the attribute error.
# print(f"Uploading research paper to {store.name}...")
# stores = list(client.file_search_stores.list())
# print(stores)
name = "fileSearchStores/earning-stocks-prediction-r-m2xknwl5mk8r"
# operation = client.file_search_stores.upload_to_file_search_store(
#     file="earning_stocks_prediction_research.docx",
#     file_search_store_name=store.name,
#     config={'display_name': 'Earning Stocks Prediction Research'}
# )

# # 3. Wait for the background processing (chunking and embedding) to finish
# while not operation.done:
#     print("Indexing research paper...")
#     time.sleep(5)
#     operation = client.operations.get(name=operation.name)

print("Research paper is now permanently indexed!")

# 4. Chat with the reference (Tool-based approach)
# We tell the model to use 'file_search' as a tool.
response = client.models.generate_content(
    model="gemini-3-pro-preview",
    contents="""
    Following research is givng us all common indicatoes that might help us to predict if a stock will explode when they deliver earnings call

So only based on the all indeicators here what would be the score for the ticker
["TRP", "ENB", "MGA", "MRNA", "DCH", "CCJ", "AAP", "NWG"]
whichh suppose to do earning call today 
don't forget any stock and for each stock give a score between 1-100 based on the indicators
    """,
    config=types.GenerateContentConfig(
        tools=[
            types.Tool(
                file_search=types.FileSearch(
                    file_search_store_names=[name]
                ),
                google_search=types.GoogleSearch()
            )
        ]
    )
)

print(f"\nGemini: {response.candidates[0].content.parts[0].text}")