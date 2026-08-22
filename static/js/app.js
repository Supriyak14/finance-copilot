// Fetches data from the API and updates the page

(function () {
  "use strict";

  const PAGE_SIZE = 15;

  const state = {
    range: "all",
    offset: 0,
    total: 0,
    categories: [],
    editingId: null, // set while the form is editing an existing row
    chatHistory: [], // [{role, content}] — the server is stateless, we own this
    aiConfigured: false,
    demoAvailable: false,
    demoModel: null, // model name that marks a stand-in reply

  };

  const $ = (id) => document.getElementById(id);

  // --- HTTP -----------------------------------------------------------

  /** fetch + JSON, turning API errors into Error objects with a usable message. */
  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });

    if (response.status === 204) return null;

    let body = null;
    try {
      body = await response.json();
    } catch {
      // Non-JSON body (proxy error page, empty 500) — fall through to the status.
    }

    if (!response.ok) {
      throw new Error(detailToMessage(body) || `Request failed (${response.status})`);
    }
    return body;
  }

  /** FastAPI returns a string detail, or a list of validation objects. */
  function detailToMessage(body) {
    if (!body || body.detail === undefined) return null;
    const { detail } = body;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => {
          const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : "";
          return field ? `${field}: ${d.msg}` : d.msg;
        })
        .join(", ");
    }
    return null;
  }

  // --- Formatting -----------------------------------------------------

  // Amounts arrive as strings (Decimal) so no precision is lost in transit.
  function money(value) {
    return Number(value).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function shortDate(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      timeZone: "UTC",
    });
  }

  function todayISO() {
    // Local calendar date, not UTC — "today" should match the user's clock.
    const now = new Date();
    const offset = now.getTimezoneOffset() * 60000;
    return new Date(now - offset).toISOString().slice(0, 10);
  }

  /** Start date for the selected range, or null for "all time". */
  function rangeStart() {
    const now = new Date();
    switch (state.range) {
      case "month":
        return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
      case "3m": {
        const d = new Date(now.getFullYear(), now.getMonth() - 2, 1);
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
      }
      case "year":
        return `${now.getFullYear()}-01-01`;
      default:
        return null;
    }
  }

  function rangeQuery() {
    const start = rangeStart();
    return start ? `?start=${start}` : "";
  }

  let toastTimer;
  function toast(message, isError = false) {
    const el = $("toast");
    el.textContent = message;
    el.classList.toggle("error", isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.hidden = true;
    }, 3200);
  }

  // --- Rendering ------------------------------------------------------

  function renderSummary(summary) {
    $("stat-income").textContent = money(summary.total_income);
    $("stat-expense").textContent = money(summary.total_expense);

    const net = $("stat-net");
    net.textContent = money(summary.net);
    net.classList.toggle("negative", Number(summary.net) < 0);

    $("stat-rate").textContent = `${summary.savings_rate}%`;
    $("stat-count").textContent =
      summary.transaction_count === 1
        ? "1 transaction"
        : `${summary.transaction_count} transactions`;
  }

  function renderAlerts(statuses) {
    const box = $("alerts");
    const flagged = statuses.filter((s) => s.status !== "ok");

    box.textContent = "";
    box.hidden = flagged.length === 0;

    for (const s of flagged) {
      const div = document.createElement("div");
      div.className = `alert ${s.status}`;

      const dot = document.createElement("span");
      dot.className = "dot";
      div.appendChild(dot);

      const text = document.createElement("span");
      text.textContent =
        s.status === "over"
          ? `You're over your ${s.category} budget — ${money(s.spent)} spent of ${money(s.monthly_limit)} this month.`
          : `${s.category} is at ${s.percent_used}% of its monthly budget — ${money(s.remaining)} left.`;
      div.appendChild(text);

      box.appendChild(div);
    }
  }

  function renderBudgets(statuses) {
    const list = $("budget-list");
    list.textContent = "";

    if (statuses.length === 0) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "No budgets set yet.";
      list.appendChild(li);
      return;
    }

    for (const s of statuses) {
      const li = document.createElement("li");
      li.className = "budget-row";

      const top = document.createElement("div");
      top.className = "budget-top";

      const name = document.createElement("span");
      name.textContent = s.category;

      const amounts = document.createElement("span");
      amounts.className = "amounts";
      amounts.textContent = `${money(s.spent)} / ${money(s.monthly_limit)}`;

      top.append(name, amounts);

      const bar = document.createElement("div");
      bar.className = `bar ${s.status}`;
      const fill = document.createElement("span");
      // Cap the bar at 100% — overspend is shown by colour, not overflow.
      fill.style.width = `${Math.min(s.percent_used, 100)}%`;
      bar.appendChild(fill);

      li.append(top, bar);
      list.appendChild(li);
    }
  }

  /** The forecast card: this month's landing point, the chart, budgets heading
   * over, and what each category is expected to cost next month. */
  function renderForecast(forecast, monthly) {
    const month = forecast.current_month;

    $("forecast-so-far").textContent = money(month.expense_so_far);
    $("forecast-expense").textContent = money(month.projected_expense);

    const net = $("forecast-net");
    net.textContent = money(month.projected_net);
    net.className = `stat-value ${Number(month.projected_net) < 0 ? "expense" : "income"}`;

    const pill = $("forecast-confidence");
    pill.hidden = false;
    pill.textContent = `${forecast.confidence} confidence`;
    pill.className = `pill ${forecast.confidence}`;

    const label = window.FinanceCharts.monthLabel;
    const day = `Day ${month.days_elapsed} of ${month.days_in_month}`;
    $("forecast-basis").textContent = forecast.months_of_history
      ? `${day}, projected from ${forecast.months_of_history} complete ` +
        `month${forecast.months_of_history === 1 ? "" : "s"} ` +
        `(${label(forecast.history_from)} – ${label(forecast.history_to)}).`
      : `${day}. No complete month of history yet, so this is a rough guess.`;

    // Recorded months, then the month in progress shown twice — what has gone
    // out so far beside where it lands — then the months still ahead.
    const points = monthly
      .filter((p) => p.month !== month.month)
      .map((p) => ({ month: p.month, actual: Number(p.expense), projected: null }));

    points.push({
      month: month.month,
      actual: Number(month.expense_so_far),
      projected: Number(month.projected_expense),
    });
    for (const step of forecast.horizon) {
      points.push({ month: step.month, actual: null, projected: Number(step.projected_expense) });
    }

    $("forecast-empty").hidden = forecast.months_of_history > 0;
    window.FinanceCharts.renderForecastChart($("forecast-chart"), points);

    renderForecastRisk(forecast.budget_risk);
    renderForecastCategories(forecast.categories);
    $("forecast-notes").textContent = forecast.notes.join(" ");
  }

  /** Only the budgets in trouble. A list of things that are fine is noise. */
  function renderForecastRisk(risks) {
    const list = $("forecast-risk");
    list.innerHTML = "";

    for (const risk of risks.filter((r) => r.status !== "on_track")) {
      const item = document.createElement("li");
      item.className = `risk ${risk.status}`;

      const name = document.createElement("strong");
      name.textContent = risk.category;

      const detail = document.createElement("span");
      detail.textContent =
        risk.status === "will_exceed"
          ? `on track for ${money(risk.projected_spend)} against a ` +
            `${money(risk.monthly_limit)} cap — over by ${money(risk.projected_overage)}`
          : `on track for ${money(risk.projected_spend)} against a ` +
            `${money(risk.monthly_limit)} cap — close`;

      item.append(name, detail);
      list.appendChild(item);
    }
  }

  function renderForecastCategories(categories) {
    const list = $("forecast-categories");
    list.innerHTML = "";

    const top = categories.filter((c) => c.type === "expense").slice(0, 6);
    $("forecast-cat-head").hidden = top.length === 0;

    for (const category of top) {
      const item = document.createElement("li");

      const name = document.createElement("span");
      name.className = "fc-name";
      name.textContent = category.category;
      if (category.kind === "fixed") {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = "bill";
        tag.title = "Lands every month for about the same amount";
        name.appendChild(tag);
      }

      const amount = document.createElement("span");
      amount.className = "fc-amount";
      amount.textContent = money(category.forecast);

      const trend = document.createElement("span");
      trend.className = `fc-trend ${category.trend}`;
      const arrow = { rising: "\u25b2", falling: "\u25bc", steady: "\u2013" }[category.trend];
      trend.textContent =
        category.trend === "steady"
          ? arrow
          : `${arrow} ${Math.abs(category.change_percent)}%`;
      trend.title =
        `Averaged ${money(category.average)} over the months used; ` +
        `${category.confidence} confidence.`;

      item.append(name, amount, trend);
      list.appendChild(item);
    }
  }

  function renderTransactions(page, append) {
    const tbody = document.querySelector("#tx-table tbody");
    if (!append) tbody.textContent = "";

    for (const tx of page.items) {
      const tr = document.createElement("tr");

      const date = document.createElement("td");
      date.textContent = shortDate(tx.date);

      const category = document.createElement("td");
      const pill = document.createElement("span");
      pill.className = "pill";
      pill.textContent = tx.category;
      category.appendChild(pill);

      const note = document.createElement("td");
      note.className = "note";
      note.textContent = tx.description || "—";
      if (tx.description) note.title = tx.description;

      const amount = document.createElement("td");
      amount.className = `amount ${tx.type}`;
      amount.textContent = `${tx.type === "income" ? "+" : "−"}${money(tx.amount)}`;

      const actions = document.createElement("td");
      actions.className = "right actions";

      const edit = document.createElement("button");
      edit.className = "btn link";
      edit.type = "button";
      edit.textContent = "\u270E";
      edit.title = "Edit";
      edit.setAttribute("aria-label", `Edit ${tx.category} transaction`);
      edit.addEventListener("click", () => startEdit(tx));
      actions.appendChild(edit);

      const del = document.createElement("button");
      del.className = "btn link";
      del.type = "button";
      del.textContent = "×";
      del.title = "Delete";
      del.setAttribute("aria-label", `Delete ${tx.category} transaction`);
      del.addEventListener("click", () => deleteTransaction(tx.id));
      actions.appendChild(del);

      tr.append(date, category, note, amount, actions);
      tbody.appendChild(tr);
    }

    const shown = tbody.childElementCount;
    $("tx-empty").hidden = page.total > 0;
    $("tx-count").textContent = page.total > 0 ? `${shown} of ${page.total}` : "";
    $("load-more").hidden = shown >= page.total;
  }

  function fillCategorySelects() {
    const type = document.querySelector('input[name="type"]:checked').value;
    const select = $("tx-category");
    const previous = select.value;

    select.textContent = "";
    for (const c of state.categories.filter((c) => c.type === type)) {
      const option = document.createElement("option");
      option.value = c.name;
      option.textContent = c.name;
      select.appendChild(option);
    }
    // Keep the choice only if it exists under the new type. Assigning a value
    // with no matching option leaves the select on selectedIndex -1, which fails
    // the `required` check and makes the submit button do nothing at all.
    const stillValid =
      previous && Array.from(select.options).some((o) => o.value === previous);
    select.selectedIndex = stillValid
      ? Array.from(select.options).findIndex((o) => o.value === previous)
      : 0;

    const budgetSelect = $("budget-category");
    if (budgetSelect.childElementCount === 0) {
      for (const c of state.categories.filter((c) => c.type === "expense")) {
        const option = document.createElement("option");
        option.value = c.name;
        option.textContent = c.name;
        budgetSelect.appendChild(option);
      }
    }
  }

  // --- Loading --------------------------------------------------------

  async function loadDashboard() {
    const query = rangeQuery();

    const [summary, byCategory, monthly, budgets, forecast] = await Promise.all([
      api(`/api/analytics/summary${query}`),
      api(`/api/analytics/by-category${query}`),
      api("/api/analytics/monthly?months=6"),
      api("/api/budgets/status"),
      api("/api/forecast?horizon=3"),
    ]);

    renderSummary(summary);
    renderAlerts(budgets);
    renderBudgets(budgets);

    $("category-empty").hidden = byCategory.length > 0;
    $("monthly-empty").hidden = monthly.length > 0;
    window.FinanceCharts.renderCategoryChart($("category-chart"), byCategory);
    window.FinanceCharts.renderMonthlyChart($("monthly-chart"), monthly);

    // The forecast reads the same monthly series, so the two charts can never
    // disagree about what a past month cost.
    renderForecast(forecast, monthly);
  }

  async function loadTransactions(append = false) {
    if (!append) state.offset = 0;

    const start = rangeStart();
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(state.offset),
    });
    if (start) params.set("start", start);

    const page = await api(`/api/transactions?${params}`);
    state.total = page.total;
    renderTransactions(page, append);
  }

  async function refresh() {
    try {
      await Promise.all([loadDashboard(), loadTransactions()]);
    } catch (err) {
      toast(err.message, true);
    }
  }

  // --- Actions --------------------------------------------------------

  /** Load an existing row into the form so it can be changed in place. */
  function startEdit(tx) {
    state.editingId = tx.id;

    // Set the type first: switching it repopulates the category list, which
    // would otherwise overwrite the category we are about to select.
    $(`type-${tx.type}`).checked = true;
    fillCategorySelects();

    $("tx-amount").value = tx.amount;

    // A row may use a category that isn't in the picklist (added via the API).
    // Without a matching option the select would be left with no selection.
    const select = $("tx-category");
    if (!Array.from(select.options).some((o) => o.value === tx.category)) {
      const option = document.createElement("option");
      option.value = tx.category;
      option.textContent = tx.category;
      select.appendChild(option);
    }
    select.value = tx.category;
    $("tx-date").value = tx.date;
    $("tx-description").value = tx.description || "";

    $("form-title").textContent = "Edit transaction";
    $("tx-submit").textContent = "Save changes";
    $("cancel-edit").hidden = false;

    $("tx-form").scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("tx-amount").focus();
  }

  function cancelEdit() {
    state.editingId = null;
    $("form-title").textContent = "Add a transaction";
    $("tx-submit").textContent = "Add transaction";
    $("cancel-edit").hidden = true;

    $("tx-amount").value = "";
    $("tx-description").value = "";
    $("tx-date").value = todayISO();
    $("suggest-note").hidden = true;
  }

  /** Submit handler for both adding a new row and saving an edited one. */
  async function saveTransaction(event) {
    event.preventDefault();
    const form = event.target;
    const button = $("tx-submit");
    const editingId = state.editingId;
    button.disabled = true;

    const payload = {
      type: form.type.value,
      amount: form.amount.value,
      category: form.category.value,
      date: form.date.value,
      description: form.description.value.trim() || null,
    };

    try {
      if (editingId === null) {
        await api("/api/transactions", { method: "POST", body: JSON.stringify(payload) });
        // Keep type/date — people usually log several at once.
        form.amount.value = "";
        form.description.value = "";
        $("suggest-note").hidden = true;
        form.amount.focus();
        toast("Transaction added");
      } else {
        await api(`/api/transactions/${editingId}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        cancelEdit();
        toast("Transaction updated");
      }
      await refresh();
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function deleteTransaction(id) {
    try {
      await api(`/api/transactions/${id}`, { method: "DELETE" });
      // Don't leave the form editing a row that no longer exists.
      if (state.editingId === id) cancelEdit();
      toast("Transaction deleted");
      await refresh();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function setBudget(event) {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;

    try {
      await api("/api/budgets", {
        method: "PUT",
        body: JSON.stringify({
          category: $("budget-category").value,
          monthly_limit: $("budget-limit").value,
        }),
      });
      $("budget-limit").value = "";
      toast("Budget saved");
      await loadDashboard();
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
    }
  }

  /** Ask the AI which category fits the note, then preselect it. */
  async function suggestCategory() {
    const description = $("tx-description").value.trim();
    const note = $("suggest-note");

    if (!description) {
      toast("Write a short note first, then ask for a suggestion", true);
      $("tx-description").focus();
      return;
    }

    const button = $("suggest-category");
    button.disabled = true;
    note.hidden = false;
    note.textContent = "Thinking…";

    const amount = $("tx-amount").value;
    try {
      const result = await api("/api/ai/categorize", {
        method: "POST",
        body: JSON.stringify({
          description,
          type: document.querySelector('input[name="type"]:checked').value,
          // Amount is optional context; only send a usable one.
          amount: amount && Number(amount) > 0 ? amount : null,
        }),
      });

      // The server only ever returns a category we already have an option for.
      $("tx-category").value = result.category;
      note.textContent = `${result.category} — ${result.reason} (${result.confidence} confidence)`;
      const badge = demoBadge(result.model);
      if (badge) {
        note.appendChild(document.createTextNode(" "));
        note.appendChild(badge);
      }
    } catch (err) {
      note.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  }

  function thinking() {
    const el = document.createElement("span");
    el.className = "dots";
    el.innerHTML = "<span></span><span></span><span></span>";
    return el;
  }

  /** A marker for a reply that came from the built-in stand-in, not the API.
   * Returns null for real replies, so callers can append unconditionally. */
  function demoBadge(model) {
    if (!state.demoModel || model !== state.demoModel) return null;
    const badge = document.createElement("span");
    badge.className = "pill demo";
    badge.textContent = "demo response";
    badge.title =
      "The Anthropic API could not be reached, so this was composed from your " +
      "own figures locally. The real integration is still wired up and is tried first.";
    return badge;
  }

  /** Writes an AI reply into a card body, badged if it is a stand-in. */
  function setAiBody(element, text, model) {
    element.className = "ai-body";
    element.textContent = "";

    const badge = demoBadge(model);
    if (badge) {
      const row = document.createElement("p");
      row.className = "ai-source";
      row.appendChild(badge);
      element.appendChild(row);
    }

    const body = document.createElement("span");
    body.className = "ai-text";
    body.textContent = text;
    element.appendChild(body);
  }

  async function getInsights() {
    const button = $("insights-btn");
    const body = $("insights-body");

    button.disabled = true;
    body.className = "ai-body muted";
    body.textContent = "";
    body.appendChild(thinking());

    try {
      const result = await api("/api/ai/insights", { method: "POST" });
      setAiBody(body, result.insights, result.model);
    } catch (err) {
      body.className = "ai-body error";
      body.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  }

  async function explainForecast() {
    const button = $("forecast-explain");
    const body = $("forecast-narrative");

    button.disabled = true;
    body.className = "ai-body muted";
    body.textContent = "";
    body.appendChild(thinking());

    try {
      const result = await api("/api/ai/forecast", { method: "POST" });
      setAiBody(body, result.narrative, result.model);
    } catch (err) {
      body.className = "ai-body error";
      body.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  }

  function appendMessage(role, text) {
    const log = $("chat-log");
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    if (text === null) {
      div.appendChild(thinking());
    } else {
      div.textContent = text;
    }
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  async function askQuestion(event) {
    event.preventDefault();
    const input = $("chat-input");
    const question = input.value.trim();
    if (!question) return;

    const button = event.target.querySelector("button[type=submit]");
    input.value = "";
    button.disabled = true;

    appendMessage("user", question);
    const placeholder = appendMessage("ai", null);

    try {
      const result = await api("/api/ai/chat", {
        method: "POST",
        body: JSON.stringify({ message: question, history: state.chatHistory }),
      });

      placeholder.textContent = result.reply;
      const badge = demoBadge(result.model);
      if (badge) {
        placeholder.appendChild(document.createTextNode("\n"));
        placeholder.appendChild(badge);
      }

      // Only record turns that completed, so a failed call can't corrupt the
      // history we replay on the next question.
      state.chatHistory.push({ role: "user", content: question });
      state.chatHistory.push({ role: "assistant", content: result.reply });
      // The API caps history at 20 entries; keep the most recent 10 exchanges.
      state.chatHistory = state.chatHistory.slice(-20);
    } catch (err) {
      placeholder.className = "msg error";
      placeholder.textContent = err.message;
    } finally {
      button.disabled = false;
      $("chat-log").scrollTop = $("chat-log").scrollHeight;
      input.focus();
    }
  }

  // --- Init -----------------------------------------------------------

  async function init() {
    $("tx-date").value = todayISO();
    $("tx-date").max = todayISO();

    $("tx-form").addEventListener("submit", saveTransaction);
    $("cancel-edit").addEventListener("click", cancelEdit);
    $("budget-form").addEventListener("submit", setBudget);
    $("insights-btn").addEventListener("click", getInsights);
    $("forecast-explain").addEventListener("click", explainForecast);
    $("suggest-category").addEventListener("click", suggestCategory);
    $("chat-form").addEventListener("submit", askQuestion);

    for (const radio of document.querySelectorAll('input[name="type"]')) {
      radio.addEventListener("change", fillCategorySelects);
    }

    $("range-select").addEventListener("change", (e) => {
      state.range = e.target.value;
      refresh();
    });

    $("load-more").addEventListener("click", async () => {
      state.offset += PAGE_SIZE;
      try {
        await loadTransactions(true);
      } catch (err) {
        state.offset -= PAGE_SIZE; // failed page shouldn't skip rows next click
        toast(err.message, true);
      }
    });

    try {
      state.categories = await api("/api/categories");
      fillCategorySelects();
    } catch (err) {
      toast(err.message, true);
    }

    // Tell the user up front if the AI half isn't configured, rather than
    // letting them click and get an error.
    try {
      const status = await api("/api/ai/status");
      state.aiConfigured = status.configured;
      state.demoModel = status.demo_model;
      state.demoAvailable = Boolean(status.demo_available);

      if (!status.configured && !status.demo_available) {
        const note = "Add an ANTHROPIC_API_KEY to your .env file and restart the server to turn on AI insights, category suggestions and chat.";
        $("insights-body").textContent = note;
        $("insights-btn").disabled = true;
        // The forecast itself needs no key — only the plain-English write-up does.
        $("forecast-narrative").textContent =
          "The forecast above is calculated without any AI. " + note;
        $("forecast-explain").disabled = true;
        $("chat-input").disabled = true;
        $("chat-input").placeholder = "AI chat needs an API key";
        $("chat-form").querySelector("button").disabled = true;
        $("suggest-category").disabled = true;
        $("suggest-category").title = "Needs an ANTHROPIC_API_KEY";
      } else if (!status.configured) {
        // No key, but a reply is still guaranteed — so nothing gets disabled.
        const note =
          "No ANTHROPIC_API_KEY is set, so these are demo replies composed from " +
          "your own figures. Add a key to .env and restart to use the real model.";
        $("insights-body").textContent = note;
        $("forecast-narrative").textContent =
          "The forecast above is calculated without any AI. " + note;
      }
    } catch {
      // Status is a nicety; the endpoints report their own errors if clicked.
    }

    await refresh();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
