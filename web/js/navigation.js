import {byId} from './dom.js';

export function bindTabs() {
  const tabs = [...byId('tabs').querySelectorAll('[role=tab]')];
  function show(current) {
    for (const tab of tabs) {
      const active = tab === current;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      byId(tab.dataset.panel).hidden = !active;
    }
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => show(tab));
    tab.addEventListener('keydown', event => {
      const target = {
        ArrowLeft: tabs[(index + tabs.length - 1) % tabs.length],
        ArrowRight: tabs[(index + 1) % tabs.length], Home: tabs[0], End: tabs.at(-1),
      }[event.key];
      if (!target) return;
      event.preventDefault();
      show(target);
      target.focus();
    });
  });
}
