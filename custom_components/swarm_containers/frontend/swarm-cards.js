/*
 * Swarm Containers dashboard cards.
 *
 * Served by the swarm_containers integration, which registers this file as a
 * dashboard resource. The cards only read entities Home Assistant already has;
 * they never talk to Portainer.
 */

const VERSION = "0.2.2";
const PLATFORM = "swarm_containers";
const MODEL = {
  swarm: "Swarm",
  node: "Swarm node",
  stack: "Swarm stack",
  service: "Swarm service",
  container: "Standalone container",
};

const STATE_ORDER = ["down", "degraded", "update_paused", "updating", "stopped", "running"];
const STATE_LABEL = {
  running: "Running",
  degraded: "Degraded",
  down: "Down",
  stopped: "Stopped",
  updating: "Updating",
  update_paused: "Update paused",
};

/* ---------- helpers ---------- */

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const num = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

function fmtPct(value) {
  const n = num(value);
  if (n === null) return "–";
  return `${n < 10 ? n.toFixed(1) : Math.round(n)}%`;
}

function fmtMiB(value) {
  const n = num(value);
  if (n === null) return "–";
  if (n >= 1024) return `${(n / 1024).toFixed(1)} GB`;
  return `${Math.round(n)} MB`;
}

function fmtAgo(iso) {
  if (!iso) return "never";
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!Number.isFinite(secs)) return "–";
  if (secs < 90) return "just now";
  if (secs < 5400) return `${Math.round(secs / 60)} min ago`;
  if (secs < 129600) return `${Math.round(secs / 3600)} h ago`;
  return `${Math.round(secs / 86400)} d ago`;
}

function stateColor(state) {
  switch (state) {
    case "running":
    case "healthy":
    case "ready":
      return "var(--success-color, #43a047)";
    case "degraded":
    case "updating":
    case "update_paused":
    case "starting":
      return "var(--warning-color, #ffa600)";
    case "down":
    case "unhealthy":
    case "dead":
    case "exited":
      return "var(--error-color, #db4437)";
    default:
      return "var(--disabled-color, #9e9e9e)";
  }
}

function stateRank(state) {
  const i = STATE_ORDER.indexOf(state);
  return i === -1 ? STATE_ORDER.length : i;
}

/** Index every swarm_containers entity by device kind and translation key. */
function buildModel(hass) {
  const model = { swarm: null, nodes: [], stacks: [], services: [], containers: [] };
  if (!hass || !hass.entities || !hass.devices) return model;
  const byDevice = new Map();
  for (const entry of Object.values(hass.entities)) {
    if (entry.platform !== PLATFORM || !entry.device_id) continue;
    if (!byDevice.has(entry.device_id)) byDevice.set(entry.device_id, {});
    const key = entry.translation_key || entry.entity_id;
    // Binary and normal sensors can share a key; prefix binary sensors to keep them apart.
    const slot = entry.entity_id.startsWith("binary_sensor.") ? `b_${key}` : entry.entity_id.startsWith("button.") ? `btn_${key}` : key;
    byDevice.get(entry.device_id)[slot] = entry.entity_id;
  }
  for (const [deviceId, e] of byDevice) {
    const device = hass.devices[deviceId];
    if (!device) continue;
    const item = { deviceId, e, name: device.name || "" };
    switch (device.model) {
      case MODEL.swarm:
        model.swarm = item;
        break;
      case MODEL.node:
        model.nodes.push(item);
        break;
      case MODEL.stack:
        item.name = item.name.replace(/^Stack /, "");
        model.stacks.push(item);
        break;
      case MODEL.service:
        item.name = item.name.replace(/^Service /, "");
        model.services.push(item);
        break;
      case MODEL.container:
        model.containers.push(item);
        break;
      default:
        break;
    }
  }
  for (const list of [model.nodes, model.stacks, model.services, model.containers]) {
    list.sort((a, b) => a.name.localeCompare(b.name));
  }
  return model;
}

const st = (hass, entityId) => (entityId ? hass.states[entityId] : undefined);
const val = (hass, entityId) => st(hass, entityId)?.state;
const attr = (hass, entityId, name) => st(hass, entityId)?.attributes?.[name];

const BASE_CSS = `
  :host { display: block; }
  ha-card { padding: 16px; box-sizing: border-box; height: 100%; }
  .header { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
  .header ha-icon { --mdc-icon-size: 26px; color: var(--primary-color); }
  .title { font-size: 1.15em; font-weight: 500; line-height: 1.2; }
  .subtitle { font-size: 0.8em; color: var(--secondary-text-color); }
  .grow { flex: 1; min-width: 0; }
  .chip { display: inline-flex; align-items: center; gap: 3px; padding: 1px 8px; border-radius: 10px; font-size: 0.75em;
          background: var(--secondary-background-color); color: var(--primary-text-color); white-space: nowrap; }
  .chip ha-icon { --mdc-icon-size: 14px; }
  .chip.warn { background: color-mix(in srgb, var(--warning-color, #ffa600) 22%, transparent); }
  .chip.bad { background: color-mix(in srgb, var(--error-color, #db4437) 22%, transparent); }
  .chip.good { background: color-mix(in srgb, var(--success-color, #43a047) 22%, transparent); }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
  .row { display: flex; align-items: center; gap: 8px; padding: 5px 2px; border-radius: 6px; cursor: pointer; min-height: 24px; }
  .row:hover { background: var(--secondary-background-color); }
  .row .name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .muted { color: var(--secondary-text-color); }
  .small { font-size: 0.8em; }
  .num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
  .group { margin-top: 10px; font-size: 0.75em; text-transform: uppercase; letter-spacing: 0.06em; color: var(--secondary-text-color);
           border-bottom: 1px solid var(--divider-color); padding-bottom: 2px; margin-bottom: 2px; }
  .empty { color: var(--secondary-text-color); text-align: center; padding: 12px 0; }
  .pills { display: flex; gap: 3px; flex-wrap: wrap; justify-content: flex-end; }
  .bar { height: 6px; border-radius: 3px; background: var(--secondary-background-color); overflow: hidden; margin-top: 3px; }
  .bar > div { height: 100%; border-radius: 3px; }
  .metric { margin: 8px 0; cursor: pointer; }
  .metric .line { display: flex; justify-content: space-between; font-size: 0.9em; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(72px, 1fr)); gap: 6px; margin: 4px 0 8px; }
  .stat { background: var(--secondary-background-color); border-radius: 8px; padding: 6px 8px; cursor: pointer; }
  .stat .v { font-size: 1.25em; font-weight: 500; }
  .stat .l { font-size: 0.72em; color: var(--secondary-text-color); }
  .problems { margin: 6px 0 0; padding: 0; list-style: none; }
  .problems li { display: flex; gap: 6px; align-items: center; font-size: 0.9em; padding: 2px 0; }
  .problems ha-icon { --mdc-icon-size: 16px; color: var(--error-color, #db4437); }
  .indent { padding-left: 18px; }
  button.action { border: none; border-radius: 16px; padding: 4px 12px; cursor: pointer; font: inherit; font-size: 0.85em;
                  background: var(--primary-color); color: var(--text-primary-color, #fff); display: inline-flex; gap: 4px; align-items: center; }
  button.action ha-icon { --mdc-icon-size: 16px; }
`;

/** Shared base: re-renders only when the entities a card reads have changed. */
class SwarmBaseCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._sig = "";
  }

  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    this._config = { ...config };
    this._sig = "";
    if (this._hass) this.hass = this._hass;
  }

  set hass(hass) {
    this._hass = hass;
    // Home Assistant can set hass before setConfig; setConfig renders once both are known.
    if (!this._config) return;
    this._model = buildModel(hass);
    const ids = this._watched();
    const sig = ids.map((id) => {
      const s = hass.states[id];
      return s ? `${s.state}|${s.last_updated}` : "-";
    }).join(";") + `#${ids.length}`;
    if (sig === this._sig) return;
    this._sig = sig;
    this._render();
  }

  /** Entity ids whose changes should trigger a re-render. */
  _watched() {
    const out = [];
    const add = (item) => item && out.push(...Object.values(item.e));
    add(this._model.swarm);
    this._model.nodes.forEach(add);
    this._model.stacks.forEach(add);
    this._model.services.forEach(add);
    this._model.containers.forEach(add);
    return out;
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  _bindClicks() {
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._moreInfo(el.dataset.entity);
      });
    });
  }

  _paint(html) {
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}${this._css ? this._css() : ""}</style><ha-card>${html}</ha-card>`;
    this._bindClicks();
  }

  _nodeByDevice(deviceId) {
    return this._model.nodes.find((n) => n.deviceId === deviceId);
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return { columns: "full", min_columns: 6 };
  }
}

/* ---------- Overview ---------- */

class SwarmOverviewCard extends SwarmBaseCard {
  static getStubConfig() {
    return {};
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        { name: "show_problems", default: true, selector: { boolean: {} } },
      ],
    };
  }

  _render() {
    const hass = this._hass;
    const sw = this._model.swarm;
    if (!sw) {
      this._paint(`<div class="empty">No Swarm Containers data. Is the integration set up?</div>`);
      return;
    }
    const e = sw.e;
    const problem = st(hass, e.b_swarm_problem);
    const reasons = problem?.attributes?.reasons || [];
    const nodesTotal = attr(hass, e.nodes_ready, "total");
    const servicesTotal = attr(hass, e.services_running, "total");
    const updates = val(hass, e.updates_available);
    const healthy = problem && problem.state === "off";
    const stat = (entity, value, label, cls = "") =>
      `<div class="stat ${cls}" data-entity="${esc(entity)}"><div class="v">${esc(value)}</div><div class="l">${esc(label)}</div></div>`;
    const showProblems = this._config.show_problems !== false;
    this._paint(`
      <div class="header">
        <ha-icon icon="mdi:docker"></ha-icon>
        <div class="grow">
          <div class="title">${esc(this._config.title || "Swarm")}</div>
          <div class="subtitle">Leader ${esc(attr(hass, e.nodes_ready, "leader") || "–")} · ${esc(attr(hass, e.nodes_ready, "managers") ?? "–")} managers</div>
        </div>
        <span class="chip ${problem ? (healthy ? "good" : "bad") : ""}" data-entity="${esc(e.b_swarm_problem)}">
          <ha-icon icon="${healthy ? "mdi:check-circle" : "mdi:alert-circle"}"></ha-icon>${healthy ? "Healthy" : problem ? `${reasons.length} problem${reasons.length === 1 ? "" : "s"}` : "–"}
        </span>
      </div>
      <div class="stats">
        ${stat(e.nodes_ready, `${val(hass, e.nodes_ready) ?? "–"}/${nodesTotal ?? "–"}`, "Nodes ready")}
        ${stat(e.services_running, `${val(hass, e.services_running) ?? "–"}/${servicesTotal ?? "–"}`, "Services")}
        ${stat(e.stacks, val(hass, e.stacks) ?? "–", "Stacks")}
        ${stat(e.tasks_running, val(hass, e.tasks_running) ?? "–", "Tasks")}
        ${stat(e.updates_available, updates === undefined || updates === "unknown" ? "–" : updates, "Updates")}
        ${stat(e.task_failures, val(hass, e.task_failures) ?? "–", "Failures 24h")}
      </div>
      ${showProblems && reasons.length
        ? `<ul class="problems">${reasons.map((r) => `<li><ha-icon icon="mdi:alert"></ha-icon>${esc(r)}</li>`).join("")}</ul>`
        : ""}
    `);
  }
}

/* ---------- Node ---------- */

class SwarmNodeCard extends SwarmBaseCard {
  static getStubConfig(hass) {
    const node = buildModel(hass).nodes[0];
    return { device_id: node ? node.deviceId : "", mode: "standard" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "device_id", required: true, selector: { device: { filter: { integration: PLATFORM, model: MODEL.node } } } },
        {
          name: "mode",
          default: "standard",
          selector: { select: { mode: "dropdown", options: ["compact", "standard", "detailed"] } },
        },
      ],
    };
  }

  setConfig(config) {
    if (!config.device_id) throw new Error("Choose a swarm node (device_id)");
    super.setConfig(config);
  }

  _watched() {
    const node = this._nodeByDevice(this._config.device_id);
    return node ? Object.values(node.e) : [];
  }

  _render() {
    const hass = this._hass;
    const node = this._nodeByDevice(this._config.device_id);
    if (!node) {
      this._paint(`<div class="empty">Swarm node not found.</div>`);
      return;
    }
    const e = node.e;
    const mode = this._config.mode || "standard";
    const role = st(hass, e.role);
    const ra = role?.attributes || {};
    const online = val(hass, e.b_online);
    const cont = st(hass, e.containers);
    const ca = cont?.attributes || {};
    const cpu = num(val(hass, e.container_cpu));
    const memPct = num(val(hass, e.container_memory_percent));
    const mem = num(val(hass, e.container_memory));
    const memTotal = num(attr(hass, e.container_memory, "memory_total_mib"));
    const top = attr(hass, e.container_cpu, "top") || [];
    const bar = (pct, color) =>
      `<div class="bar"><div style="width:${Math.min(100, Math.max(0, pct ?? 0))}%;background:${color}"></div></div>`;
    const barColor = (pct) =>
      pct === null ? "var(--disabled-color)" : pct > 85 ? "var(--error-color, #db4437)" : pct > 65 ? "var(--warning-color, #ffa600)" : "var(--success-color, #43a047)";

    const chips = [
      `<span class="chip" data-entity="${esc(e.role)}"><ha-icon icon="${ra.leader ? "mdi:crown" : "mdi:server"}"></ha-icon>${esc(ra.leader ? "Leader" : role?.state === "manager" ? "Manager" : role?.state === "worker" ? "Worker" : "–")}</span>`,
    ];
    if (ra.availability && ra.availability !== "active") chips.push(`<span class="chip warn">${esc(ra.availability)}</span>`);

    let html = `
      <div class="header">
        <div class="dot" style="background:${online === "on" ? stateColor("ready") : online === "off" ? stateColor("down") : stateColor("")}" data-entity="${esc(e.b_online)}"></div>
        <div class="grow">
          <div class="title">${esc(node.name)}</div>
          <div class="subtitle">${esc(ra.address || "")}${ra.engine_version ? ` · Docker ${esc(ra.engine_version)}` : ""}</div>
        </div>
        ${chips.join("")}
      </div>
      <div class="stats">
        <div class="stat" data-entity="${esc(e.containers)}"><div class="v">${esc(cont?.state ?? "–")}</div><div class="l">Running</div></div>
        <div class="stat" data-entity="${esc(e.containers)}"><div class="v">${esc(ca.stopped ?? "–")}</div><div class="l">Stopped</div></div>
        <div class="stat" data-entity="${esc(e.containers)}"><div class="v" style="${ca.unhealthy ? `color:${stateColor("unhealthy")}` : ""}">${esc(ca.unhealthy ?? "–")}</div><div class="l">Unhealthy</div></div>
        <div class="stat" data-entity="${esc(e.tasks)}"><div class="v">${esc(val(hass, e.tasks) ?? "–")}</div><div class="l">Swarm tasks</div></div>
      </div>`;
    if (mode !== "compact") {
      html += `
        <div class="metric" data-entity="${esc(e.container_cpu)}">
          <div class="line"><span>CPU</span><span class="num">${fmtPct(cpu)}</span></div>${bar(cpu, barColor(cpu))}
        </div>
        <div class="metric" data-entity="${esc(e.container_memory)}">
          <div class="line"><span>Memory</span><span class="num">${fmtPct(memPct)} · ${fmtMiB(mem)}${memTotal ? ` / ${fmtMiB(memTotal)}` : ""}</span></div>${bar(memPct, barColor(memPct))}
        </div>
        <div class="stats">
          <div class="stat" data-entity="${esc(e.images)}"><div class="v">${esc(val(hass, e.images) ?? "–")}</div><div class="l">Images</div></div>
          <div class="stat" data-entity="${esc(e.volumes)}"><div class="v">${esc(val(hass, e.volumes) ?? "–")}</div><div class="l">Volumes</div></div>
          <div class="stat" data-entity="${esc(e.networks)}"><div class="v">${esc(val(hass, e.networks) ?? "–")}</div><div class="l">Networks</div></div>
        </div>`;
    }
    if (mode === "detailed" && top.length) {
      html += `<div class="group">Top containers by CPU</div>` +
        top.map((t) => `<div class="row small" data-entity="${esc(e.container_cpu)}"><span class="name">${esc(t.name)}</span><span class="num muted">${fmtPct(t.cpu_percent)}</span><span class="num muted" style="min-width:56px">${fmtMiB(t.memory_mib)}</span></div>`).join("");
    }
    this._paint(html);
  }
}

/* ---------- Stacks ---------- */

class SwarmStacksCard extends SwarmBaseCard {
  static getStubConfig() {
    return { show_services: false };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        { name: "node", selector: { device: { filter: { integration: PLATFORM, model: MODEL.node } } } },
        { name: "show_services", default: false, selector: { boolean: {} } },
        { name: "hide_stopped", default: false, selector: { boolean: {} } },
        { name: "sort_by", default: "status", selector: { select: { mode: "dropdown", options: ["status", "name"] } } },
      ],
    };
  }

  _render() {
    const hass = this._hass;
    const nodeName = this._config.node ? this._nodeByDevice(this._config.node)?.name : null;
    let stacks = this._model.stacks.map((s) => {
      const status = st(hass, s.e.stack_status);
      return { ...s, status, state: status?.state, a: status?.attributes || {} };
    });
    if (nodeName) stacks = stacks.filter((s) => (s.a.nodes || []).includes(nodeName));
    if (this._config.hide_stopped) stacks = stacks.filter((s) => s.state !== "stopped");
    if ((this._config.sort_by || "status") === "status") {
      stacks.sort((a, b) => stateRank(a.state) - stateRank(b.state) || a.name.localeCompare(b.name));
    }
    const servicesByName = new Map(this._model.services.map((s) => [s.name, s]));
    const rows = stacks.map((s) => {
      const updates = s.a.updates || [];
      let row = `
        <div class="row" data-entity="${esc(s.e.stack_status)}" title="${esc(STATE_LABEL[s.state] || s.state || "")}">
          <div class="dot" style="background:${stateColor(s.state)}"></div>
          <span class="name">${esc(s.name)}</span>
          ${s.a.managed === false ? `<span class="chip">external</span>` : ""}
          ${s.state && s.state !== "running" && s.state !== "stopped" ? `<span class="chip ${s.state === "down" ? "bad" : "warn"}">${esc(STATE_LABEL[s.state] || s.state)}</span>` : ""}
          ${updates.length ? `<span class="chip warn"><ha-icon icon="mdi:arrow-up-circle"></ha-icon>${updates.length}</span>` : ""}
          ${s.a.failures_24h ? `<span class="chip bad"><ha-icon icon="mdi:alert-circle"></ha-icon>${esc(s.a.failures_24h)}</span>` : ""}
          <span class="num muted small" title="Services running">${esc(s.a.services_running ?? 0)}/${esc((s.a.services || []).length)} svc</span>
          <span class="num muted small" title="Containers" style="min-width:34px"><ha-icon icon="mdi:docker" style="--mdc-icon-size:14px"></ha-icon>${esc(s.a.containers ?? 0)}</span>
        </div>`;
      if (this._config.show_services) {
        row += (s.a.services || []).map((name) => {
          const svc = servicesByName.get(name);
          if (!svc) return "";
          const sa = attr(hass, svc.e.service_status, "running");
          const sd = attr(hass, svc.e.service_status, "desired");
          const sstate = val(hass, svc.e.service_status);
          const nodes = attr(hass, svc.e.service_status, "nodes") || [];
          return `<div class="row small indent" data-entity="${esc(svc.e.service_status)}">
              <div class="dot" style="background:${stateColor(sstate)}"></div>
              <span class="name">${esc(name.startsWith(`${s.name}_`) ? name.slice(s.name.length + 1) : name)}</span>
              <span class="pills">${nodes.map((n) => `<span class="chip">${esc(n)}</span>`).join("")}</span>
              <span class="num muted">${esc(sa ?? "–")}/${esc(sd ?? "–")}</span>
            </div>`;
        }).join("");
      }
      return row;
    });
    this._paint(`
      <div class="header">
        <ha-icon icon="mdi:layers-triple"></ha-icon>
        <div class="grow"><div class="title">${esc(this._config.title || (nodeName ? `${nodeName} stacks` : "Stacks"))}</div></div>
        <span class="muted small">${stacks.length}</span>
      </div>
      ${rows.length ? rows.join("") : `<div class="empty">No stacks.</div>`}
    `);
  }
}

/* ---------- Services ---------- */

class SwarmServicesCard extends SwarmBaseCard {
  static getStubConfig() {
    return { group_by: "stack" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        { name: "stack", selector: { device: { filter: { integration: PLATFORM, model: MODEL.stack } } } },
        { name: "node", selector: { device: { filter: { integration: PLATFORM, model: MODEL.node } } } },
        { name: "group_by", default: "stack", selector: { select: { mode: "dropdown", options: ["stack", "node", "none"] } } },
        { name: "sort_by", default: "name", selector: { select: { mode: "dropdown", options: ["name", "status", "cpu", "memory"] } } },
        { name: "include_standalone", default: false, selector: { boolean: {} } },
        { name: "show_resources", default: true, selector: { boolean: {} } },
      ],
    };
  }

  _rows() {
    const hass = this._hass;
    const rows = this._model.services.map((s) => {
      const a = attr(hass, s.e.service_status, "stack") !== undefined ? st(hass, s.e.service_status).attributes : {};
      return {
        kind: "service",
        name: s.name,
        entity: s.e.service_status,
        state: val(hass, s.e.service_status),
        stack: a.stack || "standalone",
        nodes: a.nodes || [],
        running: a.running,
        desired: a.desired,
        mode: a.mode,
        unhealthy: a.unhealthy || 0,
        cpu: num(val(hass, s.e.cpu)),
        memory: num(val(hass, s.e.memory)),
        update: val(hass, s.e.b_update_available) === "on",
      };
    });
    if (this._config.include_standalone) {
      for (const c of this._model.containers) {
        const s = st(hass, c.e.container_state);
        const a = s?.attributes || {};
        const health = val(hass, c.e.health);
        rows.push({
          kind: "container",
          name: c.name.includes(" ") ? c.name.split(" ").slice(1).join(" ") : c.name,
          entity: c.e.container_state,
          state: s?.state,
          stack: "standalone",
          nodes: a.node ? [a.node] : [],
          running: s?.state === "running" ? 1 : 0,
          desired: 1,
          mode: "container",
          unhealthy: health === "unhealthy" ? 1 : 0,
          cpu: num(val(hass, c.e.cpu)),
          memory: num(val(hass, c.e.memory)),
          update: val(hass, c.e.b_update_available) === "on",
        });
      }
    }
    return rows;
  }

  _render() {
    const cfg = this._config;
    const stackName = cfg.stack ? this._model.stacks.find((s) => s.deviceId === cfg.stack)?.name : null;
    const nodeName = cfg.node ? this._nodeByDevice(cfg.node)?.name : null;
    let rows = this._rows();
    if (stackName) rows = rows.filter((r) => r.stack === stackName);
    if (nodeName) rows = rows.filter((r) => r.nodes.includes(nodeName));
    const sortBy = cfg.sort_by || "name";
    const cmp = {
      name: (a, b) => a.name.localeCompare(b.name),
      status: (a, b) => stateRank(a.state) - stateRank(b.state) || a.name.localeCompare(b.name),
      cpu: (a, b) => (b.cpu ?? -1) - (a.cpu ?? -1),
      memory: (a, b) => (b.memory ?? -1) - (a.memory ?? -1),
    }[sortBy] || ((a, b) => a.name.localeCompare(b.name));
    rows.sort(cmp);
    const showRes = cfg.show_resources !== false;
    const groupBy = stackName && cfg.group_by !== "node" ? "none" : cfg.group_by || "stack";

    const rowHtml = (r, group) => {
      const short = groupBy === "stack" && r.name.startsWith(`${group}_`) ? r.name.slice(group.length + 1) : r.name;
      const healthy = r.state === "running" || r.state === "updating";
      return `<div class="row" data-entity="${esc(r.entity)}" title="${esc(STATE_LABEL[r.state] || r.state || "")}">
          <div class="dot" style="background:${stateColor(r.state)}"></div>
          <span class="name">${esc(short)}</span>
          ${r.update ? `<ha-icon icon="mdi:arrow-up-circle" style="--mdc-icon-size:16px;color:var(--warning-color,#ffa600)" title="Image update available"></ha-icon>` : ""}
          ${r.unhealthy ? `<ha-icon icon="mdi:heart-broken" style="--mdc-icon-size:16px;color:var(--error-color,#db4437)" title="Unhealthy"></ha-icon>` : ""}
          ${groupBy !== "node" ? `<span class="pills">${r.nodes.map((n) => `<span class="chip">${esc(n)}</span>`).join("")}</span>` : ""}
          <span class="num small ${healthy ? "muted" : ""}" style="min-width:30px${healthy ? "" : `;color:${stateColor(r.state)}`}" title="${r.mode === "global" ? "Global service" : "Running / desired"}">${r.kind === "container" ? "" : `${esc(r.running ?? "–")}/${esc(r.desired ?? "–")}${r.mode === "global" ? "G" : ""}`}</span>
          ${showRes ? `<span class="num muted small" style="min-width:40px">${fmtPct(r.cpu)}</span><span class="num muted small" style="min-width:52px">${fmtMiB(r.memory)}</span>` : ""}
        </div>`;
    };

    let body = "";
    if (!rows.length) {
      body = `<div class="empty">No services.</div>`;
    } else if (groupBy === "none") {
      body = rows.map((r) => rowHtml(r, "")).join("");
    } else {
      const groups = new Map();
      for (const r of rows) {
        const keys = groupBy === "node" ? (r.nodes.length ? r.nodes : ["not running"]) : [r.stack];
        for (const k of keys) {
          if (nodeName && groupBy === "node" && k !== nodeName) continue;
          if (!groups.has(k)) groups.set(k, []);
          groups.get(k).push(r);
        }
      }
      const names = [...groups.keys()].sort((a, b) => (a === "standalone") - (b === "standalone") || a.localeCompare(b));
      body = names.map((g) => `<div class="group">${esc(g)} · ${groups.get(g).length}</div>${groups.get(g).map((r) => rowHtml(r, g)).join("")}`).join("");
    }
    const title = cfg.title || (stackName ? `${stackName} services` : nodeName ? `${nodeName} services` : "Services");
    this._paint(`
      <div class="header">
        <ha-icon icon="mdi:cube-outline"></ha-icon>
        <div class="grow"><div class="title">${esc(title)}</div></div>
        <span class="muted small">${rows.length}</span>
      </div>
      ${body}
    `);
  }
}

/* ---------- Updates ---------- */

class SwarmUpdatesCard extends SwarmBaseCard {
  static getStubConfig() {
    return {};
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        { name: "hide_when_empty", default: false, selector: { boolean: {} } },
      ],
    };
  }

  _watched() {
    return this._model.swarm ? Object.values(this._model.swarm.e) : [];
  }

  _render() {
    const hass = this._hass;
    const sw = this._model.swarm;
    if (!sw) {
      this._paint(`<div class="empty">No Swarm Containers data.</div>`);
      return;
    }
    const sensor = st(hass, sw.e.updates_available);
    const a = sensor?.attributes || {};
    const updates = a.updates || [];
    const editing = this.closest && this.closest("hui-card-preview, hui-dialog-edit-card");
    const hidden = this._config.hide_when_empty && !updates.length && !editing;
    this.style.display = hidden ? "none" : "";
    const services = new Map(this._model.services.map((s) => [s.name, s]));
    const containers = this._model.containers;
    const rows = updates.map((u) => {
      const target = u.kind === "service"
        ? services.get(u.name)?.e.service_status
        : containers.find((c) => c.name.endsWith(` ${u.name}`) && (u.nodes || []).some((n) => c.name.startsWith(`${n} `)))?.e.container_state;
      return `<div class="row" data-entity="${esc(target || sw.e.updates_available)}">
          <ha-icon icon="mdi:arrow-up-circle" style="--mdc-icon-size:18px;color:var(--warning-color,#ffa600)"></ha-icon>
          <span class="name">${esc(u.name)}<div class="muted small">${esc(u.image)}</div></span>
          <span class="pills">${(u.nodes || []).map((n) => `<span class="chip">${esc(n)}</span>`).join("")}</span>
        </div>`;
    });
    let body;
    if (a.enabled === false) body = `<div class="empty">Image checks are turned off in the integration options.</div>`;
    else if (!a.last_checked) body = `<div class="empty">Waiting for the first image check…</div>`;
    else if (!rows.length) body = `<div class="empty"><ha-icon icon="mdi:check-circle-outline"></ha-icon> Every image is up to date.</div>`;
    else body = rows.join("");
    this._paint(`
      <div class="header">
        <ha-icon icon="mdi:package-up"></ha-icon>
        <div class="grow">
          <div class="title">${esc(this._config.title || "Image updates")}${updates.length ? ` (${updates.length})` : ""}</div>
          <div class="subtitle">Checked ${esc(fmtAgo(a.last_checked))}</div>
        </div>
        ${sw.e.btn_check_updates ? `<button class="action" id="check"><ha-icon icon="mdi:refresh"></ha-icon>Check</button>` : ""}
      </div>
      ${body}
    `);
    const btn = this.shadowRoot.getElementById("check");
    if (btn) {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        btn.disabled = true;
        hass.callService("button", "press", { entity_id: sw.e.btn_check_updates });
      });
    }
  }
}

/* ---------- registration ---------- */

const CARDS = [
  ["swarm-overview-card", SwarmOverviewCard, "Swarm overview", "Swarm health, nodes, services, tasks, updates and failures at a glance."],
  ["swarm-node-card", SwarmNodeCard, "Swarm node", "One swarm node: role, leader, containers, CPU, memory and top containers."],
  ["swarm-stacks-card", SwarmStacksCard, "Swarm stacks", "Every stack with its status, services, containers and pending updates."],
  ["swarm-services-card", SwarmServicesCard, "Swarm services", "Swarm services with replicas, placement, CPU and memory."],
  ["swarm-updates-card", SwarmUpdatesCard, "Swarm image updates", "Services and containers whose image has a newer version."],
];

window.customCards = window.customCards || [];
for (const [tag, cls, name, description] of CARDS) {
  if (!customElements.get(tag)) customElements.define(tag, cls);
  if (!window.customCards.some((c) => c.type === tag)) {
    window.customCards.push({ type: tag, name, description, preview: true, documentationURL: "https://github.com/Luca-Bachini-Barr/Portainer-Swarm-HA-integration" });
  }
}

console.info(`%c SWARM-CARDS %c ${VERSION} `, "background:#2496ed;color:#fff;font-weight:600", "background:#444;color:#fff");
