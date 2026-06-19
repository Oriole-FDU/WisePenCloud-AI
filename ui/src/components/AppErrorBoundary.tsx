import type { ErrorInfo, ReactNode } from "react";
import { Component } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "./ui/button";

type AppErrorBoundaryProps = {
  children: ReactNode;
};

type AppErrorBoundaryState = {
  error: Error | null;
};

export class AppErrorBoundary extends Component<
  AppErrorBoundaryProps,
  AppErrorBoundaryState
> {
  state: AppErrorBoundaryState = {
    error: null,
  };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ui render failed", error, errorInfo);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="app-shell">
          <div className="relative z-10 flex min-h-screen items-center justify-center p-4">
            <div className="w-full max-w-xl rounded-xl border border-red-200 bg-white p-6 shadow-[var(--app-shadow-soft)]">
              <div className="mb-4 flex items-center gap-3 text-red-700">
                <AlertTriangle className="h-5 w-5" />
                <h1 className="font-display text-lg font-semibold">UI render failed</h1>
              </div>
              <p className="mb-4 text-sm leading-7 text-slate-600">
                The preview hit a runtime error before the chat UI finished mounting.
              </p>
              <pre className="scrollbar-thin overflow-auto rounded-lg border border-slate-200 bg-slate-950 px-4 py-3 font-mono text-xs leading-6 text-slate-100">
                {this.state.error.stack || this.state.error.message}
              </pre>
              <div className="mt-4">
                <Button onClick={() => window.location.reload()} variant="primary">
                  Reload
                </Button>
              </div>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
