# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file
COPY requirements.txt ./

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Define environment variables (optional)
ENV TELEGRAM_BOT_TOKEN=''
ENV TELEGRAM_USER_ID=''

# Expose any ports if necessary (not needed in this case)

# Command to run the script
CMD ["python", "scan.py"]

