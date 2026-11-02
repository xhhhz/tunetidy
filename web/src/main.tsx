import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./index.css";

// 已打开的标签页里只改了网址的 #token= 部分时，浏览器不会重新加载页面
window.addEventListener("hashchange", () => {
  if (location.hash.includes("token=")) location.reload();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
