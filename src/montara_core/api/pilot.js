"use strict";
const $ = id => document.getElementById(id);
let account = null, project = "", summary = null, busy = false, requestKey = null;
function status(message) { $("status").textContent = message; }
function clear() {
    account = null; project = ""; summary = null; requestKey = null;
    $("panel").hidden = true; $("login").hidden = false; $("logout").hidden = true;
    $("account").textContent = ""; $("project-id").textContent = "";
    $("projects").replaceChildren(new Option("Selecciona proyecto", ""));
    $("media").replaceChildren(); $("jobs").replaceChildren(); $("recipe-state").textContent = "";
}
async function api(path, body) {
    const options = {credentials:"same-origin", cache:"no-store", redirect:"error"};
    if (body !== undefined) {
        options.method = "POST"; options.headers = {"Content-Type":"application/json"};
        options.body = JSON.stringify(body);
    }
    const res = await fetch(path, options);
    if (res.status === 401) { clear(); throw new Error("Sesión no disponible. Entra de nuevo."); }
    if (!res.ok) throw new Error(({400:"Entrada no válida",404:"Recurso no disponible",
        409:"Conflicto o límite de piloto",503:"Almacenamiento no disponible"})[res.status] || "Error de solicitud");
    return res.json();
}
function render() {
    $("project-id").textContent = project;
    $("media").replaceChildren(); $("jobs").replaceChildren();
    const media = summary ? summary.media : [];
    for (const item of media) {
        const li = document.createElement("li");
        li.textContent = item.label + " · " + item.size_bytes + " bytes";
        $("media").append(li);
    }
    $("recipe-state").textContent = summary?.recipe ? "Revisión " + summary.recipe.revision + " · " + summary.recipe.id : "Sin receta";
    for (const job of summary?.jobs || []) {
        const line = document.createElement("div"); line.className = "job";
        const text = document.createElement("span");
        text.textContent = job.state + " · intento " + job.generation + " · " + job.id + " ";
        line.append(text);
        if (job.state === "succeeded") {
            const a = document.createElement("a");
            a.href = "/api/jobs/" + encodeURIComponent(job.id) + "/artifact";
            a.textContent = "Descargar MP4"; a.download = "montara.mp4"; line.append(a);
        }
        $("jobs").append(line);
    }
    const valid = Boolean(project && summary);
    $("register").disabled = busy || !valid;
    $("recipe").disabled = busy || !valid || !["camera-a.mp4","camera-b.mp4","master.wav"].every(
        name => media.some(item => item.label === name));
    $("submit").disabled = busy || !summary?.recipe || (summary.jobs || []).some(j => ["queued","running"].includes(j.state));
    $("refresh").disabled = busy || !valid;
    $("create").disabled = busy; $("projects").disabled = busy;
}
async function loadProjects() {
    const items = await api("/api/projects");
    $("projects").replaceChildren(new Option("Selecciona proyecto",""));
    for (const item of items) $("projects").append(new Option(item.name, item.id));
    if (!items.some(item => item.id === project)) project = "";
    $("projects").value = project;
    await refresh();
}
async function refresh() {
    summary = project ? await api("/api/projects/"+encodeURIComponent(project)+"/pilot") : null;
    render();
}
async function run(action) {
    if (busy || !account) return;
    busy = true; render();
    try { await action(); status("Listo."); }
    catch (error) { status(error.message || "Operación no completada."); }
    finally { busy = false; render(); }
}
$("create").addEventListener("click", () => run(async () => {
    const p = await api("/api/projects", {name:$("name").value.trim()});
    project = p.id; requestKey = null; await loadProjects();
}));
$("projects").addEventListener("change", () => run(async () => {
    project = $("projects").value; requestKey = null; summary = null; await refresh();
}));
$("register").addEventListener("click", () => run(async () => {
    for (const [locator,kind] of [["camera-a.mp4","video"],["camera-b.mp4","video"],["master.wav","audio"]])
        await api("/api/projects/"+project+"/media",{kind,locator});
    await refresh();
}));
$("recipe").addEventListener("click", () => run(async () => {
    const id = name => summary.media.find(item => item.label === name).id;
    await api("/api/projects/"+project+"/recipes",{
        expected_revision:summary.recipe?.id || null, master:id("master.wav"),
        shots:[{representation_id:id("camera-a.mp4"),start_ms:0,end_ms:2000},
               {representation_id:id("camera-b.mp4"),start_ms:2000,end_ms:4000},
               {representation_id:id("camera-a.mp4"),start_ms:4000,end_ms:6000}]});
    requestKey = null; await refresh();
}));
$("submit").addEventListener("click", () => run(async () => {
    // Keep key across an ambiguous HTTP failure in this page; a successful
    // refresh displays the durable job. Reload never triggers submission.
    if (!requestKey) requestKey = crypto.randomUUID();
    await api("/api/projects/"+project+"/jobs",{key:requestKey,recipe_id:summary.recipe.id});
    requestKey = null; await refresh();
}));
$("refresh").addEventListener("click", () => run(refresh));
$("logout").addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    try {
        const response = await fetch("/auth/logout",{method:"POST",headers:{"Content-Type":"application/json"},
            body:"{}",credentials:"same-origin",cache:"no-store",redirect:"follow"});
        if (!response.ok) throw new Error("No se confirmó el cierre de sesión.");
        clear(); status("Sesión cerrada.");
    } catch (error) { status(error.message); }
    finally { busy = false; render(); }
});
async function init() {
    try {
        account = (await api("/api/me")).account_id;
        $("account").textContent = account; $("panel").hidden = false;
        $("login").hidden = true; $("logout").hidden = false;
        await loadProjects(); status("Sesión activa.");
    } catch (error) { status(error.message); }
}
setInterval(() => {
    if (account && project && !busy && summary?.jobs.some(j => ["queued","running"].includes(j.state)))
        run(refresh);
}, 2500);
window.addEventListener("pageshow", event => { if (event.persisted) { clear(); init(); } });
init();
