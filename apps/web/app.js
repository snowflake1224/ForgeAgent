const form = document.querySelector("#task-form");
const statusNode = document.querySelector("#status");
const resultsNode = document.querySelector("#results");

const json = (value) => JSON.stringify(value, null, 2);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusNode.textContent = "Creating task…";
  resultsNode.classList.add("hidden");
  try {
    const created = await fetch("/tasks", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        workspace_path: document.querySelector("#workspace").value,
        title: document.querySelector("#title").value,
        body: document.querySelector("#body").value,
        architecture: document.querySelector("#architecture").value,
        validation_commands: ["python -m pytest -q"]
      })
    });
    if (!created.ok) throw new Error(await created.text());
    const task = await created.json();
    statusNode.textContent = `Running ${task.task_id}…`;
    const runResponse = await fetch(`/tasks/${task.task_id}/runs`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "{}"
    });
    if (!runResponse.ok) throw new Error(await runResponse.text());
    const run = await runResponse.json();

    statusNode.textContent = `${run.run_id}: ${run.status}`;
    document.querySelector("#timeline").innerHTML = run.steps.map((step) =>
      `<div class="step ${step.success ? "" : "failed"}">
        <strong>${step.role}</strong> · ${step.skill}<br>
        <small>${step.output_summary || step.error || ""}</small>
      </div>`
    ).join("");
    document.querySelector("#diff").textContent = run.patch.diff || "(no diff)";
    document.querySelector("#tests").textContent = json(run.test_results);
    document.querySelector("#metrics").textContent = json(run.metrics);
    resultsNode.classList.remove("hidden");
  } catch (error) {
    statusNode.textContent = `Error: ${error.message}`;
  }
});
