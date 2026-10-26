/** After a new release the files an open tab was built from are gone, so a page that fails to load its code means
 *  the tab is out of date: reload it once to pick up the new release (at most once a minute, never in a loop). */
const KEY = "novara.reloaded-at";

export function reloadForNewRelease(): boolean {
  try {
    const last = Number(sessionStorage.getItem(KEY) || 0);
    if (Date.now() - last < 60_000) return false;
    sessionStorage.setItem(KEY, String(Date.now()));
  } catch {
    return false;
  }
  window.location.reload();
  return true;
}
