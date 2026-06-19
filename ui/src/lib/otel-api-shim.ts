const noopSpan = {
  spanContext() {
    return {
      traceId: "",
      spanId: "",
      traceFlags: 0,
    };
  },
  setAttribute() {
    return this;
  },
  setAttributes() {
    return this;
  },
  addEvent() {
    return this;
  },
  addLink() {
    return this;
  },
  addLinks() {
    return this;
  },
  setStatus() {
    return this;
  },
  updateName() {
    return this;
  },
  end() {
    return this;
  },
  isRecording() {
    return false;
  },
  recordException() {
    return this;
  },
};

const activeContext = {};

export const SpanStatusCode = {
  ERROR: 2,
};

export const context = {
  active() {
    return activeContext;
  },
  with<T>(_contextValue: unknown, fn: () => T) {
    return fn();
  },
};

export const trace = {
  getTracer() {
    return {
      startSpan() {
        return noopSpan;
      },
      startActiveSpan(
        _name: string,
        arg1?: unknown,
        arg2?: unknown,
        arg3?: unknown,
      ) {
        if (typeof arg1 === "function") {
          return arg1(noopSpan);
        }

        if (typeof arg2 === "function") {
          return arg2(noopSpan);
        }

        if (typeof arg3 === "function") {
          return arg3(noopSpan);
        }

        return noopSpan;
      },
    };
  },
};
