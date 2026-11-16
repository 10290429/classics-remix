// 名著改編誌｜作品頁類型篩選與上映倒數
(function () {
  // 類型篩選
  var chips = document.querySelectorAll('.chip[data-filter]');
  var items = document.querySelectorAll('.adapt');
  chips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      var f = chip.getAttribute('data-filter');
      chips.forEach(function (c) { c.classList.toggle('is-active', c === chip); });
      items.forEach(function (li) {
        var show = f === 'all' ||
          (f === 'parody' ? li.dataset.parody === 'true' : li.dataset.type === f);
        li.hidden = !show;
      });
    });
  });

  // 上映倒數（只處理有完整日期的項目）
  var today = new Date();
  today.setHours(0, 0, 0, 0);
  document.querySelectorAll('.up-date[data-date]').forEach(function (el) {
    var d = el.getAttribute('data-date');
    if (!d) return;
    var target = new Date(d + 'T00:00:00');
    var days = Math.round((target - today) / 86400000);
    var span = document.createElement('span');
    span.className = 'countdown';
    span.textContent = days > 0 ? '還有 ' + days + ' 天' : (days === 0 ? '今天推出' : '已推出，待更新');
    el.appendChild(span);
  });
})();
