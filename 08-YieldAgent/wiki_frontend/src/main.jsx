import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, Link } from "react-router-dom";

import "./styles/theme.css";
import WikiDocs from "./pages/WikiDocs.jsx";
import WikiGraph from "./pages/WikiGraph.jsx";

function Home() {
  return (
    <div className="home-shell">
      <div className="home-card">
        <h1 className="home-title">Wiki</h1>
        <p className="home-sub">vault에 누적된 fail-history 합성 결과를 탐색합니다.</p>
        <div className="home-grid">
          <Link to="/wiki/docs" className="home-link">
            <div className="home-link-icon">📖</div>
            <div className="home-link-title">Docs</div>
            <div className="home-link-sub">product → fail → oper 트리 + 본문 reader</div>
          </Link>
          <Link to="/wiki/graph" className="home-link">
            <div className="home-link-icon">🕸</div>
            <div className="home-link-title">Graph</div>
            <div className="home-link-sub">concept · episode · alias 노드 force layout</div>
          </Link>
        </div>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/wiki/docs" element={<WikiDocs />} />
        <Route path="/wiki/graph" element={<WikiGraph />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
