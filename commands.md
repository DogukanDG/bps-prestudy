
Docker Images - Installation guide:

running with docker images:
One must have docker desktop installed.
then:
clone gitlab repo (the gitlab repo link will come here later)
go to docker_prebuilt folder.
docker pull eksicek/bps_sensitivity_analysis_backend:latest
docker pull eksicek/bps_sensitivity_analysis_frontend:latest
mkdir -p output
docker compose -f docker-compose.bind.yml --env-file .env up -d

Local Installation Guide - Using Windows OS

While installing libraries, make sure that it is added to the path, environment variables. 

Install python 3.11
Python has to be <3.12
PS C:\Users\Ugur> python --version
Python 3.11.9
https://www.python.org/downloads/release/python-3119/

Install node.js for windows, using docker with npm
https://nodejs.org/en/download
PS C:\Users\Ugur> npm -v
10.8.1

Install java version 8
https://www.java.com/en/download/manual.jsp
PS C:\Users\Ugur> java -version
openjdk version "1.8.0_472"
OpenJDK Runtime Environment Corretto-8.472.08.1 (build 1.8.0_472-b08)
OpenJDK 64-Bit Server VM Corretto-8.472.08.1 (build 25.472-b08, mixed mode)


Frontend Setup Steps
Open a new terminal and navigate to the frontend folder:
Inside the frontend folder, run terminal.
Verify npm -v
If not make sure nodejs is installed and it is added to path, environment variables
If verified, 
Install frontend dependencies:
npm install
This will create a node_modules/ folder.
Start the frontend development server:
npm run dev


Backend setup steps
Verify Python 3.11 is Available
py -3.11 --version
If not make sure python 3.11 is installed and it is added to path, environment variables
If verified, 
Use the py launcher to create a venv specifically with Python 3.11:
py -3.11 -m venv venv
This creates a venv folder using Python 3.11.

venv\Scripts\activate   (later "deactivate" to close venv)

You should now see `(venv)` at the beginning of your command prompt line, like:
```
(venv) C:\Users\YourName\Desktop\bpmn-simulator>

Step 5: Verify You're Using Python 3.11
python --version
It should show Python 3.11.x now (not 3.13).

Install Dependencies
Make sure you see (venv) in your command prompt, then run:
python -m pip install --upgrade pip
Wait for it to finish, then:
pip install -r requirements.txt

Step 9: Start the Backend
python app.py
```

You should see:
```
Running with Python 3.11...
 * Serving Flask app 'app'
 * Debug mode: on
 * Running on http://127.0.0.1:5000
✅ Backend is running! Keep this Command Prompt window open.

turn debug mode on in app.py
    app.run(host="0.0.0.0", port=5000, debug=True)



later while running again cd frontend
npm run dev

later while running again cd backend
venv\Scripts\activate
python app.py



how to save docker images
Run docker desktop
login to dockerhub account

docker compose -f docker-compose.yml --env-file .env up -d --build

docker tag bps_sensitivity_analysis_backend_image:latest {dockerhub_username}/bps_sensitivity_analysis_backend:latest
docker tag bps_sensitivity_analysis_frontend_image:latest {dockerhub_username}/bps_sensitivity_analysis_frontend:latest
docker push {dockerhub_username}/bps_sensitivity_analysis_frontend:latest
docker push {dockerhub_username}/bps_sensitivity_analysis_frontend:latest





