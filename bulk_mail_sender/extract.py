import pandas as pd
import json

# Read text file
with open("Pasted text(1).txt", "r", encoding="utf-8") as file:
    data = json.load(file)

jobs = data["jobs"]

rows = []

sr_no = 1

for job in jobs:

    emails = job.get("emails", "")
    location = job.get("locations", "")

    # Handle multiple emails separated by ;
    email_list = emails.split(";")

    for email in email_list:

        rows.append({
            "Sr No": sr_no,
            "Email": email.strip(),
            "Location": location
        })

        sr_no += 1

# Create DataFrame
df = pd.DataFrame(rows)

# Remove duplicates
df = df.drop_duplicates(subset=["Email"])

# Save Excel
df.to_excel("all_emails_locations.xlsx", index=False)

print("Excel file created successfully")
print("Total Emails:", len(df))