import { token } from "./api";
import { Shell } from "./layout";
import { Home } from "./pages/Home";
import { Confirm } from "./pages/Confirm";
import { Pending } from "./pages/Pending";
import { Progress } from "./pages/Progress";
import { Result } from "./pages/Result";
import { Songs } from "./pages/Songs";
import { SplitEditor } from "./pages/SplitEditor";
import { useRoute } from "./router";

export function App() {
  const route = useRoute();
  if (!token) {
    return (
      <div className="center">
        <div>
          <h2>缺少访问口令</h2>
          <p className="sub">请在终端运行 tunetidy ui，用它打开或打印的网址访问</p>
        </div>
      </div>
    );
  }
  return <Shell>{page(route)}</Shell>;
}

function page(route: ReturnType<typeof useRoute>) {
  switch (route.name) {
    case "home":
      return <Home />;
    case "result":
      return <Result key={route.planId} planId={route.planId} />;
    case "songs":
      return <Songs key={route.planId} planId={route.planId} show={route.show} />;
    case "pending":
      return <Pending key={route.planId} planId={route.planId} />;
    case "split":
      return <SplitEditor key={`${route.planId}|${route.more}`} planId={route.planId} more={route.more} />;
    case "confirm":
      return <Confirm key={route.planId} planId={route.planId} />;
    case "progress":
      return <Progress key={route.job} planId={route.planId} jobId={route.job} />;
    case "history":
    case "undo":
      return null; // 下一步补上
  }
}
