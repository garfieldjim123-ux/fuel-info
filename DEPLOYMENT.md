# 部署验收记录

北京时间2026-11-04更新。

- 仓库代码已部署到main。
- 本地与GitHub云端33项测试通过，涵盖上涨、下降、小涨、数据缺失、5元边界、北京时间自然日、去重和有限重试。
- 公开日历及浙江92号参考价已读取原文核实并写入data/check.json；实际数据处理在提前3日窗口以外正确不发信。
- “宁波92号油价数据检查”已创建并启用：Asia/Shanghai，每天09:00，首次计划2026-11-05 09:00。首次定时执行尚待验证，调度可能延迟。
- [测试邮件云端运行](https://github.com/garfieldjim123-ux/fuel-info/actions/runs/41796809462)成功：33项测试通过；明确标注的测试邮件由QQ SMTP接受，日志记录SMTP_ACCEPTED。
- `.service/state.json`已经云端持久保存测试成功状态。没有保存邮箱、授权码或其他凭据。
- SMTP接受不等于进入收件箱，尚待用户确认测试邮件是否收到。禁止宣称收件箱送达已验证。

当前结论：代码、每日调度、SMTP实际发信和云端状态保存均已配置或验证；尚未完成首次定时数据检查验证及收件箱送达确认。

本轮测试主题为“【测试邮件】宁波92号汽油调价提醒｜非真实调价通知”，不包含真实油价预测或加油建议。[再次触发的云端验收](https://github.com/garfieldjim123-ux/fuel-info/actions/runs/41796940642)已确认日志为NO_NOTIFICATION: recommendation unchanged，未重复发送。

使用公开仓库标准Linux Actions免费方案、现有ChatGPT任务额度和QQ SMTP，没有新增付费服务。邮箱及授权码仅保存在GitHub加密Secrets；不应放入聊天或代码。修改门槛、暂停、删除步骤见README.md。
