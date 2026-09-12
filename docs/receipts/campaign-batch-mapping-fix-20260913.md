# 批量解决剩余映射异常

- 以round37的11件40SKU为范围，一次固定Web Agent读取，作业
  `329e7cae8ae745ad2b2bd78240bb517cfbe66cf6fdc73eed8f34ea6029f85a01`。
- 11件全部读到真实SKU行；40个异常物理SKU均通过完整属性、编码、价格、库存与
  原全商品导出唯一匹配，实际开关为`aria-checked=false / next-switch-off`。
  因此它们不是缺价格或需要轮换，而是本次报名文件应排除的已下架SKU。
- 仅新增带文件指纹的catalog依据，不改旧快照、价格版本、原报错或平台商品。
  ERP c4b9a44、Web Agent批量读取4c131bb已推送安装。
- ERP初次374项+115子测试通过，Web Agent相关42项通过；后续导航隐私暂停修正
  b90a034另有44项测试通过，已安装，03在无在途作业时以固定reload_idle加载，
  browser_restarted=false；没有重跑本批读取或篡改历史录像。
- 本次录像133帧、视频SHA核验一致，但有3次privacy_check超时；recording_complete=false
  保留，不因此重复全批读取，不把完整录屏验收标绿。
- 03已通过固定恢复接续11件，保护原27成功；此文件是程序修复证据，实际新增成功
  以控制器后续官方终态为准。定时仍暂停，原3价格异常和1轮换待确认不擅自重试。

## 70条优惠终态与回执接线修复

- 原作业`3832c6020c9bbc6148ecd1ec650b09fb3714ee1a5bebb8be3c880427836f5c05`
  已完成：offer `145812384556`，70成功/0失败；11件完整SKU金额读回与原文件70条
  完全一致，窗口为2026-09-28 00:00:00至2026-09-30 23:59:59。
- 录像352帧65事件0采帧错误；该作业成功不代表11件已完成后续活动报名。
- ERP在归并时报`discount_observation_changed`：录屏包装器在不可变平台回执保存后
  追加`failure_disposition`，旧验证把它当成平台内容改变。修复d644032仅分离四个
  明确传输字段（evidence_path、recording、failure_disposition、failure_disposition_path），
  所有业务字段改为双向完全一致；原claim、文件、窗口、数量、金额验证保持不变。
- 16项针对测试+4子测试通过，全scripts/tests为376项+119子测试通过。真实原回执经
  只读SQLite与原文件重新运行全部归并前校验，全部通过；没有重传优惠或清除claim。
- d644032已推送并安装，03仅调用原固定recover-discount-terminal归并原job，
  后续继续同一个controller的11件活动报名。没有恢复暂停定时或扩大SKU轮换授权。
