// Runs in <head>, before the first paint: restores the collapsed side menu without a flash.
try {
  if (localStorage.getItem("tl-sidebar") === "collapsed") {
    document.documentElement.classList.add("tl-sidebar-collapsed");
  }
} catch (error) {
  // Storage blocked (private mode, policy): the menu simply starts expanded.
}
