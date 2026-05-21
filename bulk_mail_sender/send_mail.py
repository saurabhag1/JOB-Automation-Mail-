import pandas as pd
import smtplib
import os
import time

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from dotenv import load_dotenv

# Load .env
load_dotenv()

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

# Read Excel File
df = pd.read_excel("all_emails_locations.xlsx")

# Read HTML Template
with open("template.html", "r", encoding="utf-8") as file:
    html_template = file.read()

resume_path = "Saurabh_Agrawal_2026.pdf"

# SMTP Setup
server = smtplib.SMTP("smtp.gmail.com", 587)
server.starttls()

# Login
server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)

print("Logged into Gmail successfully")

# Loop Through Emails
for index, row in df.iterrows():

    receiver_email = row["Email"]
    location = row["Location"]

    try:

        msg = MIMEMultipart()

        msg["From"] = EMAIL_ADDRESS
        msg["To"] = receiver_email
        msg["Subject"] = f"Application for DevOps Engineer Role - {location}"

        # Personalize HTML
        personalized_html = html_template.replace(
            "{{location}}",
            str(location)
        )

        msg.attach(MIMEText(personalized_html, "html"))

        # Attach Resume
        with open(resume_path, "rb") as attachment:

            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())

        encoders.encode_base64(part)

        part.add_header(
            "Content-Disposition",
            f"attachment; filename={os.path.basename(resume_path)}"
        )

        msg.attach(part)

        # Send Mail
        server.sendmail(
            EMAIL_ADDRESS,
            receiver_email,
            msg.as_string()
        )

        print(f"Sent to: {receiver_email}")

        # Delay
        time.sleep(5)

    except Exception as e:

        print(f"Failed: {receiver_email}")
        print(e)

server.quit()

print("All emails processed")
