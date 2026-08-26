# BPS Sensitivity Analysis Tool

Please refer to the following link to read the full thesis:
https://drive.google.com/file/d/181ZLqbQ1oLn72g--1Dfp4H08hHyRK0lQ/view?usp=sharing

## Installation

There are two ways to install and run the BPS Sensitivity Analysis Tool: using Docker (recommended) or local installation.

### Option 1: Docker Installation (Recommended)

#### Option 1A: Using Pre-built Docker Images

##### Prerequisites
- Docker Desktop installed on your system

##### Steps

1. Clone the repository and checkout the final branch:
   ```bash
   git clone -b final https://gitlab.com/ugureksimetu/bps_sensitivity_analysis bps_sensitivity_analysis_tool_final
   cd bps_sensitivity_analysis_tool_final
   ```

2. Navigate to the docker_prebuilt folder:
   ```bash
   cd docker_prebuilt
   ```

3. Pull the Docker images:
   ```bash
   docker pull eksicek/bps_sensitivity_analysis_backend_image_final:latest
   docker pull eksicek/bps_sensitivity_analysis_frontend_image_final:latest
   ```

4. Start the containers:
   ```bash
   docker compose -f docker-compose.bind.yml --env-file .env up -d
   ```

5. Access the application:
   - Frontend: http://localhost:5173
   - Backend API: http://localhost:5000
   - Output files will be stored in `docker_prebuilt/output/`


#### Option 1B: Building Docker Images from Source

##### Prerequisites
- Docker Desktop installed and running
- Docker Hub account (for pushing images)

##### Steps

1. Clone the repository and checkout the final branch:
   ```bash
   git clone -b final https://gitlab.com/ugureksimetu/bps_sensitivity_analysis bps_sensitivity_analysis_tool_final
   cd bps_sensitivity_analysis_tool_final
   ```

2. Navigate to the docker_build folder:
   ```bash
   cd docker_build
   ```

3. Build and start the containers:
   ```bash
   docker compose -f docker-compose.yml --env-file .env up -d --build
   ```

4. Access the application:
   - Frontend: http://localhost:5173
   - Backend API: http://localhost:5000
   - Output files will be stored in `docker_build/output/`

##### Publishing Docker Images (Optional)

If you want to publish your built images to Docker Hub:

1. Log in to your Docker Hub account:
   ```bash
   docker login
   ```

2. Tag the images with your Docker Hub username:
   ```bash
   docker tag bps_sensitivity_analysis_backend_image_final:latest {dockerhub_username}/bps_sensitivity_analysis_backend_image_final:latest
   docker tag bps_sensitivity_analysis_frontend_image_final:latest {dockerhub_username}/bps_sensitivity_analysis_frontend_image_final:latest
   ```

3. Push the images to Docker Hub:
   ```bash
   docker push {dockerhub_username}/bps_sensitivity_analysis_backend_image_final:latest
   docker push {dockerhub_username}/bps_sensitivity_analysis_frontend_image_final:latest
   ```

### Option 2: Local Installation (Windows)

#### Prerequisites

Make sure all the following software is installed and added to your system's PATH environment variables:

1. **Python 3.11** (must be <3.12)
   - Download from: https://www.python.org/downloads/release/python-3119/
   - Verify installation:
     ```bash
     python --version
     ```
     Should output: `Python 3.11.9` (or similar 3.11.x)
   - ⚠️ **Important**: If you see a different Python version, update your system PATH environment variable to prioritize Python 3.11


2. **Node.js with npm**
   - Download from: https://nodejs.org/en/download
   - Verify installation:
     ```bash
     npm -v
     # Should output: 10.8.1 (or similar)
     ```

3. **Java 8**
   - Download from: https://www.java.com/en/download/manual.jsp
   - Verify installation:
     ```bash
     java -version
     # Should output: openjdk version "1.8.0_472" (or similar)
     ```

#### Frontend Setup

1. Open a terminal and navigate to the frontend folder:
   ```bash
   cd frontend
   ```

2. Verify npm is available:
   ```bash
   npm -v
   ```

3. Install frontend dependencies:
   ```bash
   npm install
   ```
   This will create a `node_modules/` folder.

4. Start the frontend development server:
   ```bash
   npm run dev
   ```
   
   You should see:
   ```
   VITE v7.2.2  ready in 1899 ms

   ➜  Local:   http://localhost:5173/
   ➜  Network: use --host to expose
   ➜  press h + enter to show help
   ```
   
   ✅ Frontend is running! Open http://localhost:5173/ in your browser.

#### Backend Setup

1. Open a new terminal and navigate to the backend folder:
   ```bash
   cd backend
   ```

2. Verify Python 3.11 is available:
   ```bash
   python --version
   ```
   Should output: `Python 3.11.9` (or similar 3.11.x)
   
   ⚠️ **Important**: If you see a different Python version, update your system PATH environment variable to prioritize Python 3.11

3. Create a virtual environment using Python 3.11:
   ```bash
   python -m venv venv
   ```
   This creates a `venv` folder using Python 3.11.

4. Activate the virtual environment:
   ```bash
   venv\Scripts\activate
   ```
   You should now see `(venv)` at the beginning of your command prompt:
   ```
   (venv) C:\Users\YourName\Desktop\bps_sensitivity_analysis_tool>
   ```

5. Verify you're using Python 3.11:
   ```bash
   python --version
   # Should show Python 3.11.x
   ```

6. Upgrade pip:
   ```bash
   python -m pip install --upgrade pip
   ```

7. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

8. Start the backend server:
   ```bash
   python app.py
   ```
   
   You should see:
   ```
   Running with Python 3.11...
   * Serving Flask app 'app'
   * Debug mode: on
   * Running on http://127.0.0.1:5000
   ```
   
   ✅ Backend is running! Keep this terminal window open.

#### Running the Application Again

After the initial setup, you can start the application with these simplified commands:

**Frontend:**
```bash
cd frontend
npm run dev
```

**Backend:**
```bash
cd backend
venv\Scripts\activate
python app.py
```

---

## Usage

### Overview

The BPS Sensitivity Analysis Tool helps you understand how uncertainty in process parameters affects simulation results and KPIs. It combines process discovery, simulation, and sensitivity analysis into a single workflow.

### Workflow

The tool consists of three modules, typically used in sequence:

1. **SIMOD – Model Discovery**: Discover a BPMN model and parameters from an event log
2. **Sampling & Simulation**: Perturb parameters, run simulations, and compute KPIs
3. **Sensitivity Analysis & Visualization**: Quantify how each parameter influences the results

Each module can also be run independently.

---

### Module 1: SIMOD – Model Discovery

Discovers a BPMN process model and its parameters from an event log.

#### 💡 Example Inputs Available

The `example_simod_inputs/` folder contains sample event logs (BPIC 2012 and BPIC 2017) that you can use to test SIMOD.

#### Event Log Requirements

Your event log CSV file must contain the following columns:
- `case_id`
- `activity`
- `resource`
- `start_time`
- `end_time`

#### Outputs

- A BPMN model (`.bpmn` file)
- A parameters JSON file (`.json`)

Saved under: `output/simod_outputs/{folder_name}`

If no folder name is provided, it is created automatically.

#### Configuration Details

- **SIMOD version**: 5.1.6
- **Calendars**: Crisp (discrete)
- **Extraneous delays**: Disabled
- **Model discovery**: Includes optimization
- **Test log**: Not performed

For more information, visit the [SIMOD documentation](https://simod.readthedocs.io/en/latest/index.html).

---

### Module 2: Sampling & Simulation

Uses the BPMN model and parameters JSON to perform sampling and run simulations.

#### 💡 Example Inputs Available

The `example_sensitivity_analysis_inputs/` folder contains pre-generated BPMN models and parameter files (BPIC 2012 and BPIC 2017) that you can use to test sampling and simulation without running SIMOD first.

#### Sampling

Sampling means systematically perturbing parameters within their defined ranges. Each sampled configuration is simulated and KPIs are stored for analysis.

#### Sensitivity Analysis Methods

##### Sobol (Global Sensitivity Analysis)

Measures how parameters influence output variance across the full parameter space.

**Computed indices:**
- **First-order (S₁)**: Direct effect of a single parameter
- **Total-order (Sₜ)**: Overall importance, including interactions
- **Second-order (Sᵢⱼ)** (optional): Interaction effects between parameter pairs

**Number of samples:**
- Determines how many model evaluations are required to estimate global sensitivity indices
- Controls accuracy
- Should be a power of 2
- Runtime increases linearly with number of samples

##### Morris (Local Sensitivity Analysis)

A computationally cheaper method for identifying influential parameters with first-order effects only.

**Trajectories:**
- Number of random paths through the parameter space
- Each trajectory is one path where parameters are changed one at a time
- More trajectories → more reliable results

**Levels:**
- Defines the grid on which parameters can move within their range
- Affects resolution and slightly affects runtime

**Learn More:**
- [SALib Documentation](https://salib.readthedocs.io/en/latest/) - Read more about Sobol and Morris methods

#### Simulation Settings

##### Number of Cases
- One case = one complete process execution
- More cases → smoother KPIs, less noise
- **💡 Recommendation**: Use the same count as your original event log

##### Simulation Replications per Sample
- Number of simulation replications to average out randomness
- More runs → more stable KPIs, but longer runtime

##### Random Seed
- Ensures reproducibility of sampling and sensitivity analysis
- **⚠️ Note**: Prosimos simulation engine doesn't use seeds, so some variation may remain

#### Analysis Scope

Defines what counts as a dimension in sensitivity analysis.

**Examples:**
- **Parameter groups** (e.g., resources, gateways) → Dimensions = number of groups
- **Individual parameters** (e.g., gateway 1, gateway 2) → Dimensions = number of parameters

More dimensions → longer runtime.

#### Runtime Considerations

##### Sobol
Runtime grows linearly with:
- Number of dimensions (D)
- Samples (N)
- Cases (C)
- Replication runs (R)

Enabling second-order effects roughly doubles runtime.

**Formula (simplified):**
```
Runtime ~ N × (D + 2) × C × R
```

##### Morris
Runtime grows linearly with:
- Number of dimensions (D)
- Trajectories (r)
- Cases (C)
- Replication runs (R)

Significantly cheaper than Sobol.

**Formula (simplified):**
```
Runtime ~ r × (D + 1) × C × R
```

#### Outputs

All results are stored under: `output/simulation_and_sensitivity_analysis_outputs/{folder_name}`

Contents include:
- `user_config.json`: Run configuration summary
- `samples/`: Sampled parameter values
- `simulation_results/`: Computed KPIs
- `sensitivity_analysis_inputs/`: Inputs used for analysis

---

### Module 3: Sensitivity Analysis & Visualization

In the final step:

1. Select a simulation results folder (or use the latest automatically)
2. Choose a KPI and statistic
3. Run sensitivity analysis
4. Visualize the results

#### Important Notes

- Sensitivity analysis currently supports **process-level KPIs**
- Simulation outputs also include case-, task-, and resource-level KPIs for advanced analysis

Results are saved under:
```
.../{simulation_results_folder}/sensitivity_analysis_outputs/{analysis_run}
```

---

## Architecture & System Flow

### Overview

The BPS Sensitivity Analysis Tool follows a client-server architecture with a React frontend and Flask backend. The system is designed to orchestrate complex workflows involving model discovery, parameter sampling, process simulation, and sensitivity analysis.

### System Components

#### Frontend (React + Vite)
- **Location**: `frontend/src/`
- **Main Entry**: [App.jsx](frontend/src/App.jsx) - Tab-based navigation with four main panels
- **Key Components**:
  - [InstructionsPanel.jsx](frontend/src/InstructionsPanel.jsx) - User guidance and documentation
  - [SimodModelDiscovery.jsx](frontend/src/SimodModelDiscovery.jsx) - SIMOD configuration interface
  - [SamplingAndSimulation.jsx](frontend/src/SamplingAndSimulation.jsx) - Sensitivity analysis setup
  - [SensitivityAnalysis.jsx](frontend/src/SensitivityAnalysis.jsx) - Results loading and visualization
  - [Visualization.jsx](frontend/src/Visualization.jsx) - Charts and visual analysis
  - **VisualizationComponents/**: Reusable chart components (BarChart, BumpChart, Heatmap)

#### Backend (Flask + Python)
- **Location**: `backend/`
- **Main Entry**: [app.py](backend/app.py) - Flask server with REST API endpoints

**Core API Endpoints**:

1. **`POST /simod`** - SIMOD model discovery
   - Accepts: Event log CSV + configuration
   - Returns: BPMN model and parameters JSON

2. **`POST /simulate`** - Sampling and simulation pipeline
   - Accepts: BPMN + JSON + sensitivity analysis configuration
   - Returns: Simulation results and KPIs

3. **`POST /sensitivity-analysis`** - Sensitivity analysis execution
   - Accepts: KPI selection + simulation folder reference
   - Returns: Sensitivity indices (Sobol or Morris)

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                      USER INTERACTION                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   WORKFLOW MODULE 1                          │
│                  SIMOD Model Discovery                       │
├─────────────────────────────────────────────────────────────┤
│  Input: Event Log CSV                                        │
│  ↓                                                            │
│  run_simod() → SIMOD Library                                 │
│  ↓                                                            │
│  Output: BPMN Model + Parameters JSON                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   WORKFLOW MODULE 2                          │
│              Sampling & Simulation Pipeline                  │
├─────────────────────────────────────────────────────────────┤
│  Input: BPMN + JSON + SA Configuration                       │
│  ↓                                                            │
│  1. extract_parameters()                                     │
│     - Parse gateways, resources, calendars, distributions   │
│  ↓                                                            │
│  2. run_sampling()                                           │
│     - Generate Sobol/Morris samples (SALib)                 │
│  ↓                                                            │
│  3. convert_samples()                                        │
│     - Transform uniform samples to domain values            │
│  ↓                                                            │
│  4. write_all_samples_to_json_files()                        │
│     - Create individual JSON configs for each sample        │
│  ↓                                                            │
│  5. simulate_samples()                                       │
│     - Run Prosimos simulations (with replications)            │
│     - Aggregate KPIs across runs                             │
│  ↓                                                            │
│  Output: Process KPIs (Parquet files) + SA Config            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   WORKFLOW MODULE 3                          │
│          Sensitivity Analysis & Visualization                │
├─────────────────────────────────────────────────────────────┤
│  Input: Process KPIs + SA Config                             │
│  ↓                                                            │
│  1. read_sa_inputs()                                         │
│     - Load samples and problem definition                   │
│  ↓                                                            │
│  2. sobol_analysis() OR morris_analysis()                    │
│     - Compute sensitivity indices (SALib)                   │
│  ↓                                                            │
│  3. Save results as JSON                                     │
│  ↓                                                            │
│  4. Visualization components render charts                   │
│     - Bar charts, bump charts, heatmaps                     │
│  ↓                                                            │
│  Output: Sensitivity indices + Visual dashboards             │
└─────────────────────────────────────────────────────────────┘
```

### Core Backend Modules

#### 1. SIMOD Module (`src/simod/`)
**Main Function**: `run_simod(train_file, output_folder)`

- Creates directory structure (inputs/ and outputs/)
- Saves uploaded event log to inputs/
- Loads and updates configuration file
- Builds event log with preprocessing
- Executes SIMOD discovery pipeline
- Returns BPMN model and parameters JSON

**Key Output**: BPMN process model with resource calendars, gateway probabilities, and task-resource assignments

#### 2. Simulation Pipeline (`src/simulation_pipeline/`)
**Main Function**: `run_simulation_pipeline(...)`

This orchestrator coordinates five sub-modules:

**2.1 Parameter Extraction** (`extract_parameters/`)
- `extract_parameters()`: Parses JSON config to extract modifiable parameters
- Groups parameters by dimension: gateways, arrival distributions, calendars, resources
- Writes warning files for any extraction issues

**2.2 Sampling** (`sampling/`)
- `run_sampling()`: Generates sample matrices using SALib
- **Sobol**: Creates N × (D + 2) samples for global sensitivity analysis
- **Morris**: Creates r × (D + 1) samples for local sensitivity screening
- Returns SALib problem definition and sample matrix

**2.3 Sample Conversion** (`convert_samples/`)
- `convert_samples()`: Transforms uniform [0,1] samples into domain-specific values
- Handles probability distributions, calendar time slots, resource counts
- Ensures all constraints are satisfied (e.g., probabilities sum to 1)

**2.4 JSON Generation** (`convert_samples/`)
- `write_all_samples_to_json_files()`: Creates individual Prosimos config files
- Each sample becomes a complete JSON configuration
- Organized by case count for batch simulation

**2.5 Simulation** (`simulation/`)
- `simulate_samples()`: Runs Prosimos simulation engine
- Executes replication runs for each sample configuration
- Aggregates KPIs: cycle time, processing time, waiting time, etc.
- Outputs Parquet files with process-, case-, task-, and resource-level KPIs

#### 3. Sensitivity Analysis Module (`src/sensitivity_analysis/`)
**Main Function**: `run_sensitivity_analysis(...)`

- `read_sa_inputs()`: Loads problem definition and samples from previous run
- **Sobol Analysis** (`sobol_analysis.py`):
  - Computes first-order (S₁) and total-order (Sₜ) indices
  - Optional second-order interaction effects (Sᵢⱼ)
  - Identifies parameters with direct influence vs. interaction effects
- **Morris Analysis** (`morris_analysis.py`):
  - Computes μ (mean effect) and σ (standard deviation of effect)
  - Identifies parameters with linear vs. non-linear/interaction effects
  - More efficient for screening large parameter spaces

**Output**: JSON files with sensitivity indices for each parameter/group

### Key Libraries & Dependencies

- **SIMOD 5.1.6**: Process mining and model discovery
- **Prosimos**: Business process simulation engine
- **SALib**: Sensitivity analysis library (Sobol, Morris methods)
- **Pandas**: Data manipulation and KPI aggregation
- **NumPy**: Numerical computations
- **React + Vite**: Frontend framework and build tool
- **Flask + Flask-CORS**: Backend web server

### File Storage Structure

```
output/
├── simod_outputs/
│   └── {run_name}/
│       ├── inputs/          # Event log, config
│       └── outputs/         # BPMN, JSON parameters
│
└── simulation_and_sensitivity_analysis_outputs/
    └── {run_name}/
        ├── user_config.json                    # Run configuration
        ├── samples/                            # Sampled parameter values
        ├── simulation_results/                 # Prosimos outputs
        │   ├── process_kpis_*.parquet         # KPIs by case count
        │   └── ...                             # Other KPI levels
        ├── sensitivity_analysis_inputs/        # SA problem & config
        │   └── sa_config.json
        └── sensitivity_analysis_outputs/       # SA results
            └── {analysis_name}/
                ├── user_config.json
                └── *_indices.json              # Sensitivity indices
```

### Extension Points

The modular architecture allows for easy extensions:

- **New sensitivity methods**: Add to `src/sensitivity_analysis/`
- **Additional KPIs**: Extend simulation aggregation in `simulate_samples()`
- **Custom visualizations**: Add React components to `VisualizationComponents/`
- **Alternative simulators**: Replace Prosimos calls in `simulation/` module

---

