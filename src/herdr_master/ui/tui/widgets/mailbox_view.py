"""Mailbox widget rendering IHCP messages, clarification queries, and approvals."""

from rich.table import Table
from textual.widgets import Static

from herdr_master.ui.tui.state import AppState


class MailboxViewWidget(Static):
    """IHCP Mailbox view showing inter-agent communication, queries, and decisions."""

    DEFAULT_CSS = """
    MailboxViewWidget {
        background: #0f0f0f;
        padding: 0 1;
    }
    """

    def __init__(self, app_state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_state = app_state

    def on_mount(self) -> None:
        self.render_mailbox()

    def update_state(self, app_state: AppState) -> None:
        self.app_state = app_state
        self.render_mailbox()

    def render_mailbox(self) -> None:
        table = Table(
            title="[bold magenta]IHCP Inter-Harness Mailbox[/bold magenta]",
            title_justify="left",
            border_style="dim",
            expand=True,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Type", width=22)
        table.add_column("Sender", width=18)
        table.add_column("Recipient", width=18)
        table.add_column("Correlation ID", width=16)
        table.add_column("Summary / Payload", style="dim")

        envelopes = self.app_state.mailbox_envelopes
        if not envelopes:
            # Display default status message
            table.add_row(
                "HEARTBEAT_STATUS",
                "master",
                "all",
                "corr-init",
                "System initialized. Awaiting worker queries or approval requests.",
            )
        else:
            for env in envelopes:
                mtype = env.get("type", "UNKNOWN")
                sender = env.get("sender", {}).get("agent_id", "unknown")
                recipient = env.get("recipient", {}).get("agent_id", "unknown")
                corr = env.get("correlation_id", "-")
                payload = str(env.get("payload", {}))[:60]
                table.add_row(mtype, sender, recipient, corr, payload)

        self.update(table)
