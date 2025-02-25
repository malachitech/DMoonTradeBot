# Use an official lightweight Python image
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Copy only requirements.txt first to cache dependencies
COPY requirements.txt .

# Install dependencies (Optimize caching)
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Set environment variables (Optional)
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "bot.py"]
