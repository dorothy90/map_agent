import { createContext, useContext } from "react";

// standalone(wiki_frontend) 기본 경로. 호스트 앱에 임베드할 때는
// WikiRoutesProvider로 basename이 적용된 경로를 주입한다.
const DEFAULT_WIKI_ROUTES = {
  home: "/",
  docs: "/wiki/docs",
  graph: "/wiki/graph",
};

const WikiRoutesContext = createContext(DEFAULT_WIKI_ROUTES);

export const WikiRoutesProvider = WikiRoutesContext.Provider;

export function useWikiRoutes() {
  return useContext(WikiRoutesContext);
}
