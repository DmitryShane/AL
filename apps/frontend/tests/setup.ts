import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { JSDOM } from "jsdom";
const storageWindow = new JSDOM("", { url: "http://localhost" }).window;
vi.stubGlobal("localStorage", storageWindow.localStorage);
vi.stubGlobal("sessionStorage", storageWindow.sessionStorage);
afterEach(() => { cleanup(); localStorage.clear(); sessionStorage.clear(); });
