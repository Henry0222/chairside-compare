"""Readable summaries of recorded registration evidence (no inferred scores)."""
import math


def diagnostic_summary(state):
    def number(value, percent=False):
        if not isinstance(value,(int,float)) or not math.isfinite(value):
            return '未记录'
        return f'{value*100:.1f}%' if percent else f'{value:.3f}'
    names = {'target':'目标模型','current':'当前模型','initial':'初诊模型'}
    status = {'success':'通过','warning':'需复核','failed':'未通过','completed':'完成'}
    lines = [f"扫描时间：{state.get('created','未记录')}",
             f"固定参考：{names.get(state.get('reference'),state.get('reference','未记录'))}",
             '总体结果：'+('未通过，偏差显示已禁用' if state.get('failed') else '已完成；请结合下方警告复核共同牙面')]
    for key,record in state.get('registrations',{}).items():
        metrics = record.get('metrics') or {}
        lines += ['',f"【{names.get(key,key)}】",f"状态：{status.get(record.get('status'),record.get('status','未记录'))}"]
        if record.get('identical_input'):
            lines.append('与参考文件相同，跳过重复配准。')
        else:
            lines += [f"重叠率：{number(metrics.get('overlap_ratio'),True)}",
                      f"内点表面残差 RMSE：{number(metrics.get('inlier_rmse_mm'))} mm",
                      f"有效对应数量：{metrics.get('correspondence_count','未记录')}",
                      f"耗时：{number(record.get('seconds'))} 秒"]
        refinement = metrics.get('refinement') or {}
        if refinement:
            selected = refinement.get('selected',refinement.get('route','未记录'))
            lines += [f"精修状态：{status.get(refinement.get('state'),refinement.get('state','未记录'))}",
                      f"最终采用：{'初始刚性配准结果' if selected=='initial' else selected}"]
            reason = refinement.get('reason')
            if reason:
                reason = {'B_rigid_quantiles_reject':'B 方案未通过双向表面距离分位数检查，保留先前结果。'}.get(reason,reason)
                lines.append('选择原因：'+str(reason))
            for direction,title in [('target_to_source','目标→当前'),('source_to_target','当前→目标')]:
                stats = (refinement.get('validation') or {}).get(selected,{ }).get(direction,{})
                if stats:
                    lines.append(f"{title} 表面距离：中位数 {number(stats.get('median_mm'))} mm；P95 {number(stats.get('p95_mm'))} mm")
            if refinement.get('experimental'):
                lines.append('提示：自动精修选择为实验功能。')
            if refinement.get('assessment_failures'):
                lines.append('评估异常：'+str(refinement['assessment_failures']))
        warnings = record.get('warnings') or []
        for warning in warnings:
            lines.append('警告：'+('历史警告文本存在编码损坏，无法可靠还原；请查看原始诊断。' if '\ufffd' in str(warning) else str(warning)))
    if state.get('mesh_warnings'):
        lines += ['','模型检查：'+str(state['mesh_warnings'])]
    lines += ['','这些数值是表面匹配统计，并非真实位置误差；预备造成的真实形态变化也会增大表面距离。']
    return '\n'.join(lines)
