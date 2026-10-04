"""Fill the preserved official DOCX through one-part OOXML edits.

Run with the bundled document Python and its packaged OOXML helper directory.
No runtime credentials, state databases or private user information are included.
"""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

from lxml import etree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--helper-dir', required=True)
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    sys.path.insert(0, args.helper_dir)
    from docx_ooxml_patch import NS, _qn
    root = Path(args.root)
    directory = root / 'artifacts/engineering-acceptance-20260904'
    reference = directory / 'official-self-assessment-template.docx'
    output = directory / 'MOSAIC_Guard_安全自评报告.docx'
    if hashlib.sha256(reference.read_bytes()).hexdigest() != '0a506fe68def8ab6fb538a8ef66d6c4a05518849e559276c88ce3825af511014':
        raise ValueError('official template changed; inspect it before filling')
    report = (root / 'artifacts/test_report.txt').read_text()
    if '233 passed' not in report:
        raise ValueError('current test evidence differs; update the report before authoring')
    answers = {
        0: '2026 FINTECHATHON  SAFETY ASSESSMENT',
        1: 'MOSAIC Guard 安全自评报告',
        2: '评估日期为 2026 年 9 月 4 日。本报告覆盖银行智能客服的安全后端，已完成模拟部署与工程验收。客服界面、真实登录和 MFA、真实银行接入由团队其他组件提供，尚未开展生产认证。',
        6: '模型仅能提出结构化任务，由确定性规则内核授权。身份来自短期签名凭据，客户、确认、运维及审计权限分离；不同用户和会话不能使用对方的动作编号。外部资料及模型输出不能签发事实、修改可信来源或解除确认义务。查询和转账均校验账户权限，未知操作默认拒绝。',
        8: '用户日累计转账不超过 1000 元时要求确认，超过 1000 元时要求确认和独立 MFA。完整账户、金额及备注由可信界面展示，签名绑定动作摘要、用户、会话和服务端编号，并只能使用一次。提交前可凭可信取消证明停止请求，运维可撤销会话。已经提交的交易不被伪称为已撤回，须人工核账。真实 MFA 由上游认证服务完成。',
        11: '含实际容器测试的全量自动化回归为 233 项通过，代码覆盖率 87%。测试覆盖伪造确认、签名来源被改写、越权账户、跨用户和跨会话访问、重复提交及确认重放。已有 Qwen 来源注入实验保留，本轮没有新增模型调用；这些结果不代表任意自然语言攻击均可识别。客服交易状态只能来自可信工具回执，不能由模型自由生成。',
        13: '工程集成演示 16 项全部通过。真实子进程在模拟银行提交前或提交后退出，两种情况重启后均不自动重发，并锁定用户等待核账。余额、令牌、回执与安全锁可持久化。独立 Docker 验收 8 项全部通过，覆盖正常计算、无网络、宿主文件隔离、只读根目录、非 root、超时、输出及内存限制；每次确认容器已清理。',
        16: '使用构造用户和模拟账户，未处理真实银行交易。文本筛查覆盖部分密码、验证码、电话、邮箱、身份证及长账户号码。普通结果脱敏，确认界面保留必要的完整参数；内部审计不直接返回客服。状态目录权限 0700，独立密钥及主数据库权限 0600。文件权限不等于加密；真实数据授权、磁盘加密、备份和保留期限由部署方落实。没有模型可调用的审计删除接口。',
        19: '当前仅实现余额查询和转账，不能代替全队业务场景覆盖。上游来源签发、登录与 MFA 仍是可信依赖。文本筛查不是完整个人信息或语义危害检测。Docker 隔离依赖可信镜像、守护进程和宿主机内核。签名审计检查点可导出，但独立不可变存储尚须部署方提供。数据库回滚、密钥泄露、生产银行的并发及核账协议仍需上线前专项评审。接入说明和完整证据见项目 docs/26_SAFETY_BACKEND_HANDOFF_ZH.md。',
    }
    with zipfile.ZipFile(reference) as source:
        document = etree.fromstring(source.read('word/document.xml'))
        paragraphs = document.xpath('/w:document/w:body/w:p', namespaces=NS)
        def replace_text(paragraph, value, black=True):
            texts = paragraph.findall('.//w:t', NS)
            texts[0].text = value
            for element in texts[1:]:
                element.text = ''
            if black:
                for run in paragraph.findall('w:r', NS):
                    props = run.find('w:rPr', NS)
                    if props is None:
                        props = etree.Element(_qn('w', 'rPr'))
                        run.insert(0, props)
                    color = props.find('w:color', NS)
                    if color is None:
                        color = etree.SubElement(props, _qn('w', 'color'))
                    color.attrib.clear()
                    color.set(_qn('w', 'val'), '000000')
        for index, value in answers.items():
            replace_text(paragraphs[index], value)
        properties = paragraphs[1].find('w:pPr', NS)
        style = properties.find('w:pStyle', NS)
        if style is None:
            style = etree.Element(_qn('w', 'pStyle'))
            properties.insert(0, style)
        style.set(_qn('w', 'val'), 'Title')
        borders = properties.find('w:pBdr', NS)
        if borders is None:
            borders = etree.SubElement(properties, _qn('w', 'pBdr'))
        for side in ('top', 'left', 'bottom', 'right', 'between', 'bar'):
            edge = etree.SubElement(borders, _qn('w', side))
            edge.set(_qn('w', 'val'), 'nil')
        rows = document.xpath('/w:document/w:body/w:tbl/w:tr', namespaces=NS)
        for row, value in zip(rows, ('待队伍填写', '中国赛区 人工智能赛道', '待正式提交时填写')):
            replace_text(row.findall('w:tc', NS)[1].find('w:p', NS), value)
        # Keep list question labels with their answer paragraph on page breaks.
        for index in (5, 7, 10, 12, 15, 18):
            properties = paragraphs[index].find('w:pPr', NS)
            if properties.find('w:keepNext', NS) is None:
                etree.SubElement(properties, _qn('w', 'keepNext'))
        edited = etree.tostring(document, encoding='UTF-8', xml_declaration=True, standalone=True)
        inventory = []
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
            for entry in source.infolist():
                original = source.read(entry.filename)
                data = edited if entry.filename == 'word/document.xml' else original
                target.writestr(entry, data)
                inventory.append({'part': entry.filename, 'source_sha256': hashlib.sha256(original).hexdigest(),
                    'output_sha256': hashlib.sha256(data).hexdigest(), 'changed': original != data})
    if [item['part'] for item in inventory if item['changed']] != ['word/document.xml']:
        raise AssertionError('unexpected package changes')
    (directory / 'document-package-audit.json').write_text(json.dumps(inventory, indent=2))
    print(output)


if __name__ == '__main__':
    main()
