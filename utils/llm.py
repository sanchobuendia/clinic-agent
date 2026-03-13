from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv()


def model_aws():
    return init_chat_model(
        "us.anthropic.claude-3-5-sonnet-20240620-v1:0",
        model_provider="bedrock_converse",
        temperature=0.2,
    )
