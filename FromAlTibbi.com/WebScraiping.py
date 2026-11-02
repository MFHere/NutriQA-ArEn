import requests
from tqdm import tqdm
from bs4 import BeautifulSoup
from Scriping import count_total_pages,extract_QA,validate_csv_path,initialize_csv,append_row_to_csv


fieldnames=["Questions","Answers","Category"]
categories=["مرض-السكري"]



csv_location=input('Please Enter desired csv path : ')
csv_location=validate_csv_path(csv_location)
initialize_csv(csv_location,fieldnames)



# Website:
main_website = "https://altibbi.com/اسئلة-طبية"

for category in categories:
    url=main_website+"/"+category
    num_pages=count_total_pages(url)
    
    print(f"There is {num_pages} in category \"{category}\" ")

    for page in tqdm(range(num_pages),desc=f"{category}"):

        headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        website_with_page=url+f"?page={page}"
        # Send request
        response = requests.get(website_with_page, headers=headers)

        # Check response
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")

            articles=soup.find_all("article",class_="new-question-item mb-20-mobile")

            for article in articles:
                url_QA=article.find('a',class_="d-block see-answer-button-fqaa mr-auto")['href']
                Q,A=extract_QA(url_QA)
                if Q !=None and A !=None:
                    append_row_to_csv(csv_location,{"Questions":Q,"Answers":A,"Category":category})

        else:

            print(f"Error: Received response code in webpage request : {response.status_code}")
