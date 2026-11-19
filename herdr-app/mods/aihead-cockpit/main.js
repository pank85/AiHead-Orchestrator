import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { agents, terminals, spaces, panes, theme, run } from "herdr";

export default class AiHeadCockpit extends View {
  render() {
    const t = theme();
    const list = agents();
    const current = spaces().find((space) => space.active);
    const allPanes = panes();
    const currentPanes = current ? allPanes.filter((pane) =>
      current.tabs.some((tab) => tab.id === pane.tab) && pane.terminal) : [];
    const listed = new Set(list.map((a) => a.address));
    const allTerminals = terminals();
    const heads = allTerminals.filter((terminal) => terminal.state === "live" &&
      typeof terminal.address === "string" && terminal.address && terminal.labels?.["aihead.role"] === "head");
    const children = allTerminals.filter((terminal) =>
      terminal.parent && terminal.state === "live" && !listed.has(terminal.address));
    const statuses = ["blocked", "working", "done", "idle"];

    const row = (item, child) => {
      const status = typeof item.status === "string" ? item.status : item.status?.state;
      return h_flex()
      .id(`${child ? "child" : "agent"}-${item.address}`)
      .px(8).py(6).gap(8).rounded(6).items_center()
      .cursor_pointer().hover((el) => el.bg(t.hover))
      .on_click(() => child || !item.pane
        ? run("terminal.peek", { terminal: item.address })
        : run("pane.focus", { pane: item.pane }))
      .child(v_flex().flex_1().min_w(0)
        .child(div().truncate().text_color(t.foreground)
          .child(item.title || item.name || item.program?.name || item.address))
        .child(div().truncate().text_size(11).text_color(t.dim)
          .child(child ? `Child of ${item.parent}  |  ${item.address}` : `${item.space_name || "Agent"}  |  ${item.address}`)))
      .child(div().text_size(11).text_color(t[status] || t.muted)
        .child(status || (child ? "live" : "unknown")));
    };

    return v_flex().size_full().min_h(0).bg(t.chrome).text_size(13)
      .child(v_flex().p(12).gap(4).flex_none()
        .child(div().font_semibold().text_color(t.foreground).child("AiHead Cockpit"))
        .child(div().text_size(11).text_color(t.dim).child("Herdr live data - not an AiHead job list"))
        .child(div().text_size(11).text_color(t.muted)
          .child(statuses.map((status) => `${status}: ${list.filter((a) => (typeof a.status === "string" ? a.status : a.status?.state) === status).length}`).join("  |  "))))
      .child(v_flex().id("cockpit-list").flex_1().min_h(0).px(6).overflow_y_scrollbar().mode("always")
        .child(div().px(8).py(6).text_size(11).text_color(t.dim).child("Current space"))
        .when(!current, (el) => el.child(div().px(8).py(6).text_color(t.muted).child("No space selected.")))
        .when(!!current, (el) => el
          .child(div().px(8).text_color(t.foreground).child(current.name))
          .child(div().px(8).py(6).text_size(11).text_color(t.muted)
            .child(`${current.machine || "Local"}  |  ${current.tabs.length} tabs  |  ${currentPanes.length} terminal panes`))
          .children(current.tabs.map((tab) => div().id(`tab-${tab.id}`).px(8).py(6)
            .rounded(6).cursor_pointer().text_color(t.foreground)
            .hover((item) => item.bg(t.hover))
            .on_click(() => run("tab.select", { id: tab.id }))
            .child(`Open tab: ${tab.name}  |  ${tab.status || "no reported status"}`))))
        .child(div().px(8).py(6).text_size(11).text_color(t.dim).child(`Agents (${list.length})`))
        .children(list.map((agent) => row(agent, false)))
        .when(list.length === 0, (el) => el.child(div().px(8).py(6).text_color(t.muted)
          .child("No reported agents. Agents appear when they report status to Herdr.")))
        .child(div().px(8).py(6).text_size(11).text_color(t.dim).child(`AiHead terminals (${heads.length})`))
        .children(heads.map((head) => {
          const pane = allPanes.find((item) => item.terminal === head.address && item.pane);
          return h_flex().id(`aihead-${head.address}`).px(8).py(6).gap(8).rounded(6)
            .cursor_pointer().hover((el) => el.bg(t.hover))
            .on_click(() => pane ? run("pane.focus", { pane: pane.pane }) :
              run("terminal.peek", { terminal: head.address }))
            .child(div().truncate().text_color(t.foreground).child(head.title || head.name || head.address))
            .child(div().text_size(11).text_color(t.muted).child(head.address));
        }))
        .when(heads.length === 0, (el) => el.child(div().px(8).py(6).text_color(t.muted)
          .child("No live labeled AiHead terminals.")))
        .child(div().px(8).py(6).text_size(11).text_color(t.dim).child(`Other live child terminals (${children.length})`))
        .children(children.map((child) => row(child, true))));
  }
}
