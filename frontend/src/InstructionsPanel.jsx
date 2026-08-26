/**
 * InstructionsPanel
 * ------------------
 * Pure presentational component: no state, no side effects.
 */

export default function InstructionsPanel() {
  return (
    <div className="bg-white/90 rounded-2xl border border-gray-100 p-6 space-y-4">

      <h3 className="font-semibold text-gray-900 text-lg">
        BPS Sensitivity Analysis Tool
      </h3>

      <p className="text-sm text-gray-700 leading-relaxed">
        This tool helps you understand how uncertainty in process parameters affects 
        simulation results and KPIs. It combines process discovery, simulation, and 
        sensitivity analysis into a single workflow.
      </p>

      <hr />

      <h4 className="font-semibold text-gray-900">
        Workflow Overview
      </h4>

      <p className="text-sm text-gray-600">
        The tool consists of three modules, typically used in sequence:
      </p>

      <ol className="list-decimal list-inside text-sm text-gray-700 space-y-2 ml-2">
        <li>
          <strong>SIMOD – Model Discovery:</strong> Discover a BPMN model and parameters from an event log
        </li>
        <li>
          <strong>Sampling & Simulation:</strong> Perturb parameters, run simulations, and compute KPIs
        </li>
        <li>
          <strong>Sensitivity Analysis:</strong> Quantify how much each parameter influences the results
        </li>
      </ol>

      <p className="text-sm text-gray-600 italic">
        Each module can also be run independently.
      </p>

      <hr />

      <h4 className="font-semibold text-gray-900">
        1. SIMOD – Model Discovery
      </h4>

      <p className="text-sm text-gray-700">
        Discovers a BPMN process model and its parameters from an event log.
      </p>

      <div className="bg-blue-50 border-l-4 border-blue-400 p-3 my-3">
        <p className="text-sm font-medium text-gray-900 mb-1">💡 Example Inputs Available</p>
        <p className="text-sm text-gray-700">
          The <code className="bg-white px-1 rounded text-xs">example_simod_inputs/</code> folder 
          contains sample event logs (BPIC 2012 and BPIC 2017) that you can use to test SIMOD.
        </p>
      </div>

      <div className="ml-4 space-y-3">
        <div>
          <p className="text-sm font-medium text-gray-900 mb-1">
            Required Event Log Columns
          </p>
          <ul className="list-disc list-inside text-sm text-gray-600 space-y-0.5">
            <li><code className="bg-gray-100 px-1 rounded">case_id</code></li>
            <li><code className="bg-gray-100 px-1 rounded">activity</code></li>
            <li><code className="bg-gray-100 px-1 rounded">resource</code></li>
            <li><code className="bg-gray-100 px-1 rounded">start_time</code></li>
            <li><code className="bg-gray-100 px-1 rounded">end_time</code></li>
          </ul>
        </div>

        <div>
          <p className="text-sm font-medium text-gray-900 mb-1">
            Outputs
          </p>
          <ul className="list-disc list-inside text-sm text-gray-600">
            <li>BPMN model (.bpmn file)</li>
            <li>Parameters JSON file</li>
          </ul>
          <p className="text-sm text-gray-600 mt-2">
            Saved to: <code className="bg-gray-100 px-1 rounded text-xs">output/simod_outputs/{`{folder_name}`}</code>
            <br />
            <span className="text-xs italic">Folder name is auto-generated if not provided.</span>
          </p>
        </div>

        <div>
          <p className="text-sm font-medium text-gray-900 mb-1">
            Configuration
          </p>
          <ul className="list-disc list-inside text-sm text-gray-600 space-y-0.5">
            <li>SIMOD version 5.1.6</li>
            <li>Crisp (discrete) calendars</li>
            <li>Extraneous delays disabled</li>
            <li>Includes optimization</li>
            <li>No test log evaluation</li>
          </ul>
          <p className="text-sm text-gray-600 mt-2">
            <a
              href="https://simod.readthedocs.io/en/latest/index.html"
              className="text-blue-600 hover:text-blue-700 underline"
              target="_blank"
              rel="noreferrer"
            >
              Learn more about SIMOD →
            </a>
          </p>
        </div>
      </div>

      <hr />

      <h4 className="font-semibold text-gray-900">
        2. Sampling & Simulation
      </h4>

      <p className="text-sm text-gray-700 mb-3">
        Takes the BPMN model and parameters, then systematically perturbs parameters 
        within defined ranges. Each sampled configuration is simulated to compute KPIs.
      </p>

      <div className="bg-blue-50 border-l-4 border-blue-400 p-3 my-3">
        <p className="text-sm font-medium text-gray-900 mb-1">💡 Example Inputs Available</p>
        <p className="text-sm text-gray-700">
          The <code className="bg-white px-1 rounded text-xs">example_sensitivity_analysis_inputs/</code> folder 
          contains pre-generated BPMN models and parameter files (BPIC 2012 and BPIC 2017) that you can use 
          to test sampling and simulation without running SIMOD first.
        </p>
      </div>

      <div className="ml-4 space-y-4">
        <div>
          <h5 className="font-medium text-gray-900 text-sm mb-2">
            Sensitivity Analysis Methods
          </h5>

          <div className="space-y-3">
            <div className="bg-blue-50 p-3 rounded">
              <p className="text-sm font-medium text-gray-900">
                Sobol (Global Sensitivity Analysis)
              </p>
              <p className="text-sm text-gray-700 mt-1">
                Measures how parameters influence output variance across the entire parameter space.
              </p>
              
              <p className="text-xs font-medium text-gray-800 mt-2 mb-1">Computed Indices:</p>
              <ul className="list-disc list-inside text-sm text-gray-700 space-y-0.5">
                <li><strong>First-order (S₁):</strong> Direct effect of a single parameter</li>
                <li><strong>Total-order (Sₜ):</strong> Overall importance, including interactions</li>
                <li><strong>Second-order (Sᵢⱼ):</strong> Pairwise interaction effects (optional)</li>
              </ul>

              <p className="text-xs font-medium text-gray-800 mt-2 mb-1">Number of Samples:</p>
              <ul className="list-disc list-inside text-sm text-gray-700 space-y-0.5">
                <li>Controls accuracy of sensitivity indices</li>
                <li>Should be a power of 2 (e.g., 256, 512, 1024)</li>
                <li>Runtime increases linearly with sample count</li>
              </ul>
            </div>

            <div className="bg-green-50 p-3 rounded">
              <p className="text-sm font-medium text-gray-900">
                Morris (Local Sensitivity Analysis)
              </p>
              <p className="text-sm text-gray-700 mt-1">
                Computationally cheaper method for identifying influential parameters. 
                Focuses on first-order effects only.
              </p>

              <p className="text-xs font-medium text-gray-800 mt-2 mb-1">Trajectories:</p>
              <ul className="list-disc list-inside text-sm text-gray-700 space-y-0.5">
                <li>Number of random paths through parameter space</li>
                <li>Each trajectory changes one parameter at a time</li>
                <li>More trajectories → more reliable results</li>
              </ul>

              <p className="text-xs font-medium text-gray-800 mt-2 mb-1">Levels:</p>
              <ul className="list-disc list-inside text-sm text-gray-700 space-y-0.5">
                <li>Defines the grid resolution within parameter ranges</li>
                <li>Affects granularity, minimal impact on runtime</li>
              </ul>
            </div>
          </div>

          <p className="text-sm text-gray-700 mt-3">
            <a
              href="https://salib.readthedocs.io/en/latest/"
              className="text-blue-600 hover:text-blue-700 underline"
              target="_blank"
              rel="noreferrer"
            >
              Read more about Sobol and Morris →
            </a>
          </p>
        </div>

        <div>
          <h5 className="font-medium text-gray-900 text-sm mb-2">
            Simulation Settings
          </h5>

          <div className="space-y-2">
            <div>
              <p className="text-sm font-medium text-gray-900">Number of Cases</p>
              <ul className="list-disc list-inside text-sm text-gray-700">
                <li>One case = one complete process execution</li>
                <li>More cases → smoother KPIs, less noise</li>
              </ul>
              <p className="text-xs italic text-gray-600 mt-1">
                💡 Recommendation: Use the same count as your original event log
              </p>
            </div>

            <div>
              <p className="text-sm font-medium text-gray-900">Replication Runs</p>
              <ul className="list-disc list-inside text-sm text-gray-700">
                <li>Number of simulation replications to average out randomness</li>
                <li>More runs → more stable KPIs, but longer runtime</li>
              </ul>
            </div>

            <div>
              <p className="text-sm font-medium text-gray-900">Random Seed</p>
              <ul className="list-disc list-inside text-sm text-gray-700">
                <li>Ensures reproducibility of sampling and sensitivity analysis</li>
                <li>⚠️ Note: Prosimos simulation engine doesn't use seeds, so some variation may remain</li>
              </ul>
            </div>
          </div>
        </div>

        <div>
          <h5 className="font-medium text-gray-900 text-sm mb-2">
            Analysis Scope
          </h5>
          <p className="text-sm text-gray-700 mb-2">
            Defines what counts as a dimension in sensitivity analysis:
          </p>
          <ul className="list-disc list-inside text-sm text-gray-700 space-y-1">
            <li>
              <strong>Parameter groups</strong> (e.g., resources, gateways) 
              → Dimensions = number of groups
            </li>
            <li>
              <strong>Individual parameters</strong> (e.g., gateway 1, gateway 2) 
              → Dimensions = number of parameters
            </li>
          </ul>
          <p className="text-xs italic text-gray-600 mt-1">
            More dimensions → longer runtime
          </p>
        </div>
      </div>

      <hr />

      <h4 className="font-semibold text-gray-900">
        Runtime Estimates
      </h4>

      <div className="space-y-3 ml-4">
        <div className="bg-gray-50 p-3 rounded">
          <p className="text-sm font-medium text-gray-900 mb-1">Sobol Method</p>
          <p className="text-sm text-gray-700">
            Runtime grows linearly with: dimensions (D), samples (N), cases (C), and replication runs (R)
          </p>
          <p className="text-xs font-mono text-gray-600 mt-2">
            Runtime ~ N × (D + 2) × C × R
          </p>
          <p className="text-xs text-gray-600 mt-1 italic">
            Enabling second-order effects roughly doubles runtime
          </p>
        </div>

        <div className="bg-gray-50 p-3 rounded">
          <p className="text-sm font-medium text-gray-900 mb-1">Morris Method</p>
          <p className="text-sm text-gray-700">
            Runtime grows linearly with: dimensions (D), trajectories (r), cases (C), and replication runs (R)
          </p>
          <p className="text-xs font-mono text-gray-600 mt-2">
            Runtime ~ r × (D + 1) × C × R
          </p>
          <p className="text-xs text-gray-600 mt-1 italic">
            Significantly faster than Sobol
          </p>
        </div>
      </div>

      <hr />

      <h4 className="font-semibold text-gray-900">
        Outputs & Results
      </h4>

      <p className="text-sm text-gray-700 mb-2">
        All results are saved to:
      </p>
      <pre className="bg-gray-50 text-xs p-2 rounded border border-gray-200 overflow-x-auto">
output/simulation_and_sensitivity_analysis_outputs/{`{folder_name}`}
      </pre>

      <p className="text-sm text-gray-700 mt-3 mb-1">Contents include:</p>
      <ul className="list-disc list-inside text-sm text-gray-700 space-y-0.5">
        <li><code className="bg-gray-100 px-1 rounded text-xs">user_config.json</code> – Run configuration summary</li>
        <li><code className="bg-gray-100 px-1 rounded text-xs">samples/</code> – Sampled parameter values</li>
        <li><code className="bg-gray-100 px-1 rounded text-xs">simulation_results/</code> – Computed KPIs</li>
        <li><code className="bg-gray-100 px-1 rounded text-xs">sensitivity_analysis_inputs/</code> – Analysis inputs</li>
      </ul>

      <hr />

      <h4 className="font-semibold text-gray-900">
        3. Sensitivity Analysis & Visualization
      </h4>

      <p className="text-sm text-gray-700 mb-2">
        In the final step:
      </p>

      <ol className="list-decimal list-inside text-sm text-gray-700 space-y-1 ml-2">
        <li>Select a simulation results folder (or use the latest automatically)</li>
        <li>Choose a KPI and statistic</li>
        <li>Run sensitivity analysis</li>
        <li>Visualize the results</li>
      </ol>

      <div className="bg-yellow-50 border-l-4 border-yellow-400 p-3 mt-3">
        <p className="text-sm font-medium text-gray-900 mb-1">Important Notes</p>
        <ul className="list-disc list-inside text-sm text-gray-700 space-y-0.5">
          <li>Sensitivity analysis currently supports <strong>process-level KPIs</strong></li>
          <li>Simulation outputs also include case-, task-, and resource-level KPIs for advanced analysis</li>
        </ul>
      </div>

      <p className="text-sm text-gray-700 mt-3">
        Results are saved to:
      </p>
      <pre className="bg-gray-50 text-xs p-2 rounded border border-gray-200 overflow-x-auto">
.../{`{simulation_results_folder}`}/sensitivity_analysis_outputs/{`{analysis_run}`}
      </pre>

    </div>
  );
}
