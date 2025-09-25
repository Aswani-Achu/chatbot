# 1️⃣ Use a lightweight Python image
FROM python:3.12-slim

# 2️⃣ Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 3️⃣ Create & set the working directory inside the container
WORKDIR /app

# 4️⃣ Copy only requirements first (better cache usage)
COPY requirement.txt .

# 5️⃣ Install dependencies without caching
RUN pip install --no-cache-dir -r requirement.txt

# 6️⃣ Copy the rest of the project files
COPY . .

# 7️⃣ Expose the port (Flask default)
EXPOSE 5000

# 8️⃣ Default command to run your Flask app
CMD ["python", "app.py"]
