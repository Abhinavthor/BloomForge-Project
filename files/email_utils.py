import smtplib
import random
import os
import streamlit as st
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def get_smtp_config():
    """Retrieve SMTP configuration from Streamlit secrets or Environment variables."""
    config = {
        "server": "smtp.gmail.com",
        "port": 587,
        "user": "",
        "password": ""
    }
    
    # Try secrets first
    try:
        if "SMTP_USER" in st.secrets:
            config["user"] = st.secrets["SMTP_USER"]
        if "SMTP_PASSWORD" in st.secrets:
            config["password"] = st.secrets["SMTP_PASSWORD"]
        if "SMTP_SERVER" in st.secrets:
            config["server"] = st.secrets["SMTP_SERVER"]
        if "SMTP_PORT" in st.secrets:
            config["port"] = int(st.secrets["SMTP_PORT"])
    except Exception:
        pass
    
    # Fallback to env vars if not set in secrets
    if not config["user"]:
        config["user"] = os.environ.get("SMTP_USER", "")
    if not config["password"]:
        config["password"] = os.environ.get("SMTP_PASSWORD", "")
    if os.environ.get("SMTP_SERVER"):
        config["server"] = os.environ.get("SMTP_SERVER")
    if os.environ.get("SMTP_PORT"):
        config["port"] = int(os.environ.get("SMTP_PORT"))
        
    return config

def generate_otp():
    """Generate a 6-digit random OTP."""
    return str(random.randint(100000, 999999))

def send_otp_email(recipient_email: str, otp: str, action: str = "login") -> bool:
    """Send an OTP email to the recipient and return whether it actually succeeded."""
    config = get_smtp_config()

    if not config["user"] or not config["password"]:
        st.error("SMTP Configuration missing. Please set SMTP_USER and SMTP_PASSWORD.")
        return False

    sender_email = config["user"]

    message = MIMEMultipart("alternative")
    message["Subject"] = f"Your BloomForge OTP for {action.capitalize()}"
    message["From"] = sender_email
    message["To"] = recipient_email

    text = f"""
    Hello,

    Your One-Time Password (OTP) for {action} is: {otp}

    This code will expire shortly. Please do not share it with anyone.

    - BloomForge Team
    """

    part = MIMEText(text, "plain")
    message.attach(part)

    try:
        with smtplib.SMTP(config["server"], config["port"]) as server:
            server.starttls()
            server.login(config["user"], config["password"])
            server.sendmail(sender_email, recipient_email, message.as_string())
        return True
    except Exception as e:
        st.error(f"Failed to send OTP email. Check SMTP settings: {e}")
        print(f"Failed to send email: {e}")
        return False
