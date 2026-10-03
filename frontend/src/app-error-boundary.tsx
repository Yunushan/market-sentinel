import { Component } from "react";
import type { ReactNode } from "react";

export class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <main className="workspace">
          <h1>MarketSentinel</h1>
          <div className="error-banner" role="alert">
            The interface could not display the current data. Reload to retrieve a fresh snapshot and review any pending operation before retrying it.
          </div>
          <button className="icon-button" onClick={() => window.location.reload()}>Reload interface</button>
        </main>
      );
    }
    return this.props.children;
  }
}
