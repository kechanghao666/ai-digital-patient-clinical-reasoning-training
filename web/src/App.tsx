import { useEffect, useState, type ReactNode } from "react";
import { get, getIdentity, onIdentityChange, request, setIdentity, type Identity } from "./api";
import { Login } from "./pages/Login";
import { Account, ForcedPasswordChange } from "./pages/Account";
import { Tasks } from "./pages/Tasks";
import { Library } from "./pages/Library";
import { MyTrainings } from "./pages/MyTrainings";
import { Training } from "./pages/Training";
import { Classes } from "./pages/teacher/Classes";
import { Dashboard } from "./pages/teacher/Dashboard";
import { CaseManager } from "./pages/teacher/CaseManager";
import { FeedbackReview } from "./pages/teacher/FeedbackReview";
import { ScoreReview } from "./pages/teacher/ScoreReview";

export interface Route { path: string[]; params: URLSearchParams; }
function parse(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  return { path: path.split("/").filter(Boolean), params: new URLSearchParams(query) };
}
let unsavedGuard: (() => boolean) | null = null;
export function setUnsavedGuard(fn: (() => boolean) | null) { unsavedGuard = fn; }
export function navigate(to: string) {
  if (unsavedGuard && !unsavedGuard()) return;
  window.location.hash = to;
}
function useRoute() {
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export const NOTICE = "教学模拟环境，非真实患者，不用于真实诊疗。评分与反馈仅供参考，以带教医师意见为准。";

export function Header({ identity, onLogout }: { identity?: Identity | null; onLogout?: () => void }) {
  const [mode, setMode] = useState<string | null>(null);
  useEffect(() => { get<{ patient_adapter: string }>("/health").then((h) => setMode(h.patient_adapter)).catch(() => setMode(null)); }, []);
  const name = identity ? identity.user.display_name || identity.user.username : "";
  return (
    <header className="header">
      <span className="product">临床思维训练系统</span>
      <span className="env">病人引擎：{mode === "nexent" ? "Nexent 智能体" : mode === "simulator" ? "本地规则引擎" : "服务未连接"}</span>
      {identity && <div className="user">
        <span><b>{name}</b> <span className="role">· {identity.user.role === "teacher" ? "教师" : "学生"}{identity.mode === "demo" ? "（演示）" : ""}</span></span>
        {identity.mode === "local" && !identity.user.must_change_password && <a href="#/account" style={{ color: "inherit" }}>账号设置</a>}
        <button onClick={onLogout}>退出</button>
      </div>}
    </header>
  );
}

export const canTrain = (identity: Identity) => !(identity.mode === "demo" && identity.user.role === "teacher");

function navFor(identity: Identity) {
  const local = identity.mode === "local";
  const groups: { title: string; items: { to: string; label: string }[] }[] = [];
  if (canTrain(identity)) groups.push({ title: "训练", items: [
    ...(local && identity.user.role === "student" ? [{ to: "/tasks", label: "我的任务" }] : []),
    { to: "/library", label: "病例库" }, { to: "/trainings", label: "训练记录" }] });
  if (identity.user.role === "teacher") groups.push({ title: "教学管理", items: [
    ...(local ? [{ to: "/teacher/classes", label: "班级与任务" }] : []),
    { to: "/teacher/dashboard", label: "学情统计" }, { to: "/teacher/cases", label: "病例管理" },
    { to: "/teacher/scores", label: "评分复核" }, { to: "/teacher/feedback", label: "反馈复核" }] });
  return groups;
}
function home(identity: Identity) {
  if (identity.user.role === "teacher") return identity.mode === "local" ? "/teacher/classes" : "/teacher/dashboard";
  return identity.mode === "local" ? "/tasks" : "/library";
}

export function App() {
  const [identity, setLocal] = useState<Identity | null>(getIdentity);
  const route = useRoute();
  useEffect(() => { const off = onIdentityChange(() => setLocal(getIdentity())); return () => { off(); }; }, []);
  useEffect(() => {
    if (identity?.mode === "local") get("/api/auth/me").then((user) => setIdentity({ ...identity, user })).catch(() => setIdentity(null));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const logout = async () => {
    if (identity?.mode === "local") await request("POST", "/api/auth/logout").catch(() => undefined);
    setIdentity(null);
    window.dispatchEvent(new Event("app:clear-toasts"));
    window.location.hash = "";
  };
  if (!identity) return <Login />;
  if (identity.user.must_change_password) return <ForcedPasswordChange identity={identity} onLogout={logout} />;

  const [section, sub, id] = route.path;
  const current = "/" + route.path.join("/");
  const teacher = identity.user.role === "teacher";
  let page: ReactNode = null;
  if (section === "training" && sub && canTrain(identity)) page = <Training sessionId={sub} identity={identity} />;
  else if (section === "tasks" && identity.mode === "local") page = <Tasks />;
  else if (section === "library" && canTrain(identity)) page = <Library />;
  else if (section === "trainings" && canTrain(identity)) page = <MyTrainings />;
  else if (section === "account" && identity.mode === "local") page = <Account identity={identity} />;
  else if (section === "teacher" && teacher) {
    page = sub === "classes" && identity.mode === "local" ? <Classes classId={id} />
      : sub === "cases" ? <CaseManager />
      : sub === "feedback" ? <FeedbackReview key={route.params.toString()} params={route.params} />
      : sub === "scores" ? <ScoreReview key={route.params.toString()} params={route.params} />
      : <Dashboard params={route.params} />;
  }
  if (!page) {
    window.location.replace("#" + home(identity));
    return null;
  }
  const groups = navFor(identity);
  const active = (to: string) => current === to || current.startsWith(to + "/") || (to === "/trainings" && section === "training");
  const link = (item: { to: string; label: string }) => (
    <a key={item.to} href={`#${item.to}`} aria-current={active(item.to) ? "page" : undefined}
      onClick={(e) => { e.preventDefault(); navigate(item.to); }}>{item.label}</a>
  );
  return (
    <div className="app">
      <Header identity={identity} onLogout={logout} />
      <div className="strip" role="note">{NOTICE}</div>
      <div className="body">
        <nav className="nav" aria-label="主导航">
          {groups.map((g) => <div key={g.title}><div className="nav-title">{g.title}</div>{g.items.map(link)}</div>)}
        </nav>
        <div style={{ minWidth: 0 }}>
          <nav className="mobile-nav" aria-label="主导航">{groups.flatMap((g) => g.items).map(link)}</nav>
          <main className="content" id="main">{page}</main>
        </div>
      </div>
    </div>
  );
}
