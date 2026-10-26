import requests

# Define base URL for datasets API
base_url = "https://api.semanticscholar.org/datasets/v1/release/"

# To get the list of available releases make a request to the base url. No additional parameters needed.
session = requests.Session()
# requests adds its default User-Agent; Scholar's S2 API contract omits that header.
session.headers.pop("User-Agent")
response = session.get(base_url)

# Print the response data
print(response.json())
