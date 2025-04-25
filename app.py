import os
import time
from dotenv import load_dotenv
from typing import List, Dict, Tuple

import streamlit as st
from notion_client import Client
from langchain.memory import ConversationBufferMemory
from langchain.chains import ConversationalRetrievalChain
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain.vectorstores import FAISS
from langchain.docstore.document import Document

# Load environment variables
load_dotenv()

class Config:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    NOTION_API_KEY = os.getenv("NOTION_API_KEY")
    NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

    if not all([GEMINI_API_KEY, NOTION_API_KEY, NOTION_DATABASE_ID]):
        raise ValueError("Missing keys in .env file")

    NOTION_FIELDS = {
        "user_id": ("User Id", "title"),
        "first_name": ("First Name", "Text"),
        "last_name": ("Last Name", "Text"),
        "sex": ("Sex", "Select"),
        "email": ("Email", "Email"),
        "phone": ("Phone", "Text"),
        "job_title": ("Job Title", "Text"),
        "dob": ("Date of birth", "Date"),
    }

class NotionClient:
    def __init__(self):
        self.client = Client(auth=Config.NOTION_API_KEY)

    def fetch_all_records(self) -> List[Dict]:
        all_results = []
        start_cursor = None
        has_more = True

        while has_more:
            response = self.client.databases.query(
                database_id=Config.NOTION_DATABASE_ID,
                start_cursor=start_cursor,
            )
            batch = response.get("results", [])
            all_results.extend(batch)
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")
            time.sleep(0.1)

        return all_results

class DataProcessor:
    @staticmethod
    def normalize_gender(gender: str) -> str:
        if not isinstance(gender, str):
            return "Unknown"
        gender = gender.lower().strip()
        if "female" in gender:
            return "Female"
        if "male" in gender:
            return "Male"
        return "Other"

    @staticmethod
    def extract_field(props: Dict, field_name: str, field_type: str) -> str:
        field_data = props.get(field_name, {})
        if field_type == "title":
            title_content = field_data.get("title", [{}])
            return title_content[0].get("text", {}).get("content", "N/A") if title_content else "N/A"
        elif field_type == "Text":
            text_content = field_data.get("rich_text", [{}])
            return text_content[0].get("text", {}).get("content", "N/A") if text_content else "N/A"
        elif field_type == "Select":
            select_data = field_data.get("select", {})
            return select_data.get("name", "N/A") if isinstance(select_data, dict) else "N/A"
        elif field_type == "Date":
            date_data = field_data.get("date", {})
            return date_data.get("start", "N/A")
        else:
            return field_data.get(field_type.lower(), "N/A")

    def process_records(self, notion_data: List[Dict]) -> Tuple[List[Document], List[Dict]]:
        docs = []
        employees = []

        for item in notion_data:
            props = item.get("properties", {})

            employee = {
                "user_id": self.extract_field(props, *Config.NOTION_FIELDS["user_id"]),
                "name": f"{self.extract_field(props, *Config.NOTION_FIELDS['first_name'])} "
                        f"{self.extract_field(props, *Config.NOTION_FIELDS['last_name'])}",
                "sex": self.normalize_gender(self.extract_field(props, *Config.NOTION_FIELDS["sex"])),
                "email": self.extract_field(props, *Config.NOTION_FIELDS["email"]),
                "phone": self.extract_field(props, *Config.NOTION_FIELDS["phone"]),
                "job_title": self.extract_field(props, *Config.NOTION_FIELDS["job_title"]),
                "dob": self.extract_field(props, *Config.NOTION_FIELDS["dob"]),
            }

            if employee["name"].strip() != "N/A N/A":
                employees.append(employee)
                docs.append(Document(page_content=(f"EMPLOYEE RECORD {employee['user_id']}\n"
                                                    f"Name: {employee['name']}\n"
                                                    f"Gender: {employee['sex']}\n"
                                                    f"Email: {employee['email']}\n"
                                                    f"Phone: {employee['phone']}\n"
                                                    f"Position: {employee['job_title']}\n"
                                                    f"DOB: {employee['dob']}")))

        return docs, employees

class AIEngine:
    def __init__(self, docs: List[Document], employees: List[Dict]):
        self.embeddings = GoogleGenerativeAIEmbeddings(
            model="models/embedding-001",
            google_api_key=Config.GEMINI_API_KEY
        )
        self.vectorstore = FAISS.from_documents(docs, self.embeddings)

        self.memory = ConversationBufferMemory(
            memory_key="chat_history",
            return_messages=True
        )

        self.qa_chain = ConversationalRetrievalChain.from_llm(
            llm=ChatGoogleGenerativeAI(
                model="models/gemini-1.5-flash",
                google_api_key=Config.GEMINI_API_KEY,
                temperature=0.3,
                convert_system_message_to_human=True
            ),
            retriever=self.vectorstore.as_retriever(search_kwargs={"k": 5}),
            memory=self.memory
        )

        self.employees = employees

    def query(self, question: str) -> Dict:
        time.sleep(1.0)
        question_lower = question.lower()

        # Checking for specific employee details
        for emp in self.employees:
            full_name = emp["name"].lower()
            if full_name in question_lower:
                response_lines = []

                if "email" in question_lower:
                    response_lines.append(f"Email: {emp['email']}")
                if "phone" in question_lower:
                    response_lines.append(f"Phone: {emp['phone']}")
                if "job title" in question_lower:
                    response_lines.append(f"Job Title: {emp['job_title']}")
                if "date of birth" in question_lower or "dob" in question_lower:
                    response_lines.append(f"Date of Birth: {emp['dob']}")
                if "user id" in question_lower:
                    response_lines.append(f"User ID: {emp['user_id']}")
                if "details" in question_lower:
                    response_lines.append(
                        f"Details:\nName: {emp['name']}\nUser ID: {emp['user_id']}\n"
                        f"Email: {emp['email']}\nSex: {emp['sex']}\nPhone: {emp['phone']}\n"
                        f"Date of Birth: {emp['dob']}\nJob Title: {emp['job_title']}"
                    )

                if response_lines:
                    return {"answer": "\n".join(response_lines)}

        # If no specific employee is queried, handle general queries
        if "how many employees are male and female" in question_lower:
            male = sum(1 for e in self.employees if e["sex"] == "Male")
            female = sum(1 for e in self.employees if e["sex"] == "Female")
            return {"answer": f"Male: {male}, Female: {female}"}

        return self.qa_chain.invoke({"question": question})

# Streamlit UI
def main():
    st.set_page_config(page_title="Knowledge Management System for Enterprises Chatbot", page_icon="🤖")
    st.title("Knowledge Management System for Enterprises Chatbot")
    st.markdown("Ask questions about your employee records!")

    notion = NotionClient()
    processor = DataProcessor()
    raw_data = notion.fetch_all_records()
    docs, employees = processor.process_records(raw_data)
    bot = AIEngine(docs, employees)

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    query = st.text_input("Ask a question about employees:")
    if query:
        response = bot.query(query)
        st.session_state.chat_history.append(("You", query))
        st.session_state.chat_history.append(("Assistant", response["answer"]))

    for sender, msg in st.session_state.chat_history:
        st.markdown(f"**{sender}:** {msg}")

if __name__ == "__main__":
    main()
