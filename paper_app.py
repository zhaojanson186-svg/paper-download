import streamlit as st
import os
import time
import pandas as pd

from config import HISTORY_FILE, DOWNLOAD_DIR, load_history, save_history, sanitize_filename
from engine_ai import init_ai_model, list_available_gemini_models, analyze_paper_with_ai, analyze_patent_with_ai
from engine_gdrive import get_gdrive_service, upload_to_gdrive
# 把原来的导入替换为：
from engine_scraper import search_pmc_oa, download_pdf, fetch_pmc_metadata, search_europe_pmc_patents, get_last_patent_fetch_debug

st.set_page_config(page_title="双擎 AI 情报终端", layout="wide", page_icon="🧠")

st.title("🧠 药物研发 AI 全景情报终端")
st.markdown("文献精读与专利防线双向覆盖。自动抓取、LLM 智能提纯、去重并直传云盘。")

history = load_history()

# ==========================================
# 侧边栏：配置与状态
# ==========================================
with st.sidebar:
    st.header("⚙️ 全局配置")
    gdrive_folder_id = st.text_input("📁 Google Drive 文件夹 ID", placeholder="粘贴你的文件夹ID")
    st.markdown("---")
    
    gemini_model_name = st.text_input("🤖 Gemini model id", value="gemini-1.5-flash-latest")
    
    # 动态获取密钥以解耦引擎
    gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")
    gcp_token = st.secrets.get("GCP_TOKEN", "")

    if st.button("🔄 刷新可用模型列表", type="secondary"):
        with st.spinner("正在拉取可用模型列表..."):
            models = list_available_gemini_models(gemini_api_key, max_items=50)
            if models:
                st.success(f"获取到 {len(models)} 个模型。")
                st.expander("可用模型列表").code("\n".join(models))
            else:
                st.warning("未能获取模型列表。")

    ai_model = init_ai_model(gemini_api_key, gemini_model_name)
    if ai_model:
        st.success("🤖 AI 双擎提纯：已激活")
    else:
        st.error("🤖 AI 双擎提纯：离线 (需配置 GEMINI_API_KEY)")

    st.markdown("---")
    debug_mode = st.checkbox("🧪 显示 AI 解析调试信息", value=False, key="debug_ai_parse")
    st.write(f"📖 云端总账本记录数: **{len(history)}** 条")
    if st.button("🗑️ 清空历史记录", type="secondary"):
        if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
        st.success("账本已彻底重置！")
        time.sleep(1)
        st.rerun()

tab1, tab2 = st.tabs(["📄 核心文献直传 + AI 精读", "💡 专利雷达 + AI 构型拆解"])

# ========================================================
# 引擎 1：文献抓取 + AI 精读报表
# ========================================================
with tab1:
    st.markdown("### 🧬 学术前沿：抓取原文并生成 AI 精读报表")
    query_paper = st.text_input("输入检索关键词 (靶点/适应症)", value="CD3 bispecific antibody", key="q_paper")
    max_papers = st.number_input("本次请求最大篇数", min_value=1, max_value=500, value=15)
    
    if st.button("🚀 开始极速抓取并进行 AI 提纯", type="primary"):
        if not query_paper or not gdrive_folder_id:
            st.error("请确保已填写关键词和 Google Drive 文件夹 ID！")
        elif not ai_model:
            st.error("请先激活 AI 引擎！")
        else:
            with st.spinner("正在初始化网盘与文献数据库..."):
                drive_service, err = get_gdrive_service(gcp_token)
                
            if not drive_service:
                st.error(f"网盘授权失败: {err}")
            else:
                all_pmc_ids = search_pmc_oa(query_paper, max_papers)
                new_pmc_ids = [pid for pid in all_pmc_ids if f"PMC_{pid}" not in history]
                
                if not new_pmc_ids:
                    st.info("🔕 本次未发现新的开源文献。")
                else:
                    st.write(f"✅ 发现 **{len(new_pmc_ids)}** 篇新文献！正在处理...")
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    paper_report_data = []
                    
                    for i, pmcid in enumerate(new_pmc_ids):
                        status_text.text(f"🤖 处理第 {i+1}/{len(new_pmc_ids)} 篇 (PMC{pmcid})...")
                        
                        status, local_path, file_name = download_pdf(pmcid, query_paper)
                        pdf_uploaded = "未上传"
                        if status == "下载成功":
                            is_up, _ = upload_to_gdrive(drive_service, local_path, file_name, gdrive_folder_id)
                            pdf_uploaded = "✅ 原文已入库" if is_up else "❌ 上传失败"
                            os.remove(local_path)
                        
                        title, abstract = fetch_pmc_metadata(pmcid)
                        ai_insights = analyze_paper_with_ai(ai_model, abstract, debug_mode)
                        
                        paper_report_data.append({
                            "文献编号": f"PMC{pmcid}",
                            "🎯核心靶点": ai_insights.get("靶点组合", ""),
                            "🐁实验模型": ai_insights.get("实验模型", ""),
                            "💡核心结论": ai_insights.get("AI核心结论", ""),
                            "原文状态": pdf_uploaded,
                            "文献标题": title,
                            "官方直达链接": f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/"
                        })
                        
                        history[f"PMC_{pmcid}"] = f"✅ 已精读 (PDF入库状态: {pdf_uploaded})"
                        save_history(history)
                        
                        time.sleep(4.5) # 防封限速红线
                        progress_bar.progress((i + 1) / len(new_pmc_ids))
                        
                    status_text.text("🧠 所有文献精读完毕！正在推送总报表...")
                    df_papers = pd.DataFrame(paper_report_data)
                    st.dataframe(df_papers, column_config={"官方直达链接": st.column_config.LinkColumn()}, use_container_width=True, hide_index=True)
                    
                    csv_name = f"{sanitize_filename(query_paper)}_Paper_AI_Report_{time.strftime('%m%d_%H%M')}.csv"
                    csv_path = os.path.join(DOWNLOAD_DIR, csv_name)
                    df_papers.to_csv(csv_path, index=False, encoding="utf-8-sig")
                    
                    is_up, _ = upload_to_gdrive(drive_service, csv_path, csv_name, gdrive_folder_id, 'text/csv')
                    if is_up:
                        st.success("🎉 任务完美结束！原文 PDF 及《文献精读报表》已推送到 Google Drive！")
                        os.remove(csv_path)

# ========================================================
# 引擎 2：专利情报抓取 + AI 深度提纯
# ========================================================
with tab2:
    st.markdown("### 🧠 竞争对手挖掘：大模型提炼核心管线情报")
    query_patent = st.text_input("输入检索关键词 (如靶点、技术全称)", value="CD3 bispecific antibody", key="q_patent")
    max_patents = st.number_input("需梳理的专利数量", min_value=1, max_value=200, value=30)
    
    if st.button("📊 生成【专利 AI 提纯】报表并推送", type="primary"):
        if not query_patent or not gdrive_folder_id:
            st.error("请确保已填写关键词和 Google Drive 文件夹 ID！")
        elif not ai_model:
            st.error("AI 引擎未激活！")
        else:
            with st.spinner("正在提取底层专利数据并比对账本..."):
                drive_service, err = get_gdrive_service(gcp_token)
                if not drive_service:
                    st.error(f"网盘授权失败: {err}")
                else:
                    # 把这行：
# patents = search_google_patents(query_patent, max_patents)
# 改成下面这行：
patents = search_europe_pmc_patents(query_patent, max_patents)
                    
                    if not patents:
                        debug = get_last_patent_fetch_debug()
                        st.warning(f"⚠️ 未能抓取到专利数据（HTTP {debug.get('status_code', '未知')}）。请稍等后重试。")
                    else:
                        new_patents = [pt for pt in patents if f"PAT_{pt['全球公开号']}" not in history]
                        
                        if not new_patents:
                            st.info("🔕 扫描到的专利均已在历史账本中，无需重复提取！")
                        else:
                            st.write(f"✅ 发现 **{len(new_patents)}** 项新专利！大模型正在逐篇拆解...")
                            ai_progress = st.progress(0)
                            ai_status = st.empty()
                            
                            for idx, pt in enumerate(new_patents):
                                ai_status.text(f"🤖 AI 提纯第 {idx+1}/{len(new_patents)} 项: {pt['全球公开号']} ...")
                                ai_insights = analyze_patent_with_ai(ai_model, pt['核心摘要'], debug_mode)
                                
                                pt["🎯靶点组合"] = ai_insights.get("靶点组合", "未提取")
                                pt["🧬抗体构型"] = ai_insights.get("抗体构型", "未提取")
                                pt["💡商业一句话总结"] = ai_insights.get("AI一句话总结", "未提取")

                                history[f"PAT_{pt['全球公开号']}"] = "✅ 已AI提纯"
                                
                                time.sleep(4.5) # 防封限速红线
                                ai_progress.progress((idx + 1) / len(new_patents))
                            
                            ai_status.text("🧠 提纯完毕！正在生成全景竞争报表...")
                            save_history(history)
                            
                            cols = ["全球公开号", "申请公司 / 拥有者", "🎯靶点组合", "🧬抗体构型", "💡商业一句话总结", "优先权/申请日", "专利名称", "核心摘要", "直达阅读链接"]
                            df_patents = pd.DataFrame(new_patents)[cols]
                            st.dataframe(df_patents, column_config={"直达阅读链接": st.column_config.LinkColumn()}, use_container_width=True, hide_index=True)
                            
                            csv_name = f"{sanitize_filename(query_patent)}_Patent_AI_Report_{time.strftime('%m%d_%H%M')}.csv"
                            csv_path = os.path.join(DOWNLOAD_DIR, csv_name)
                            df_patents.to_csv(csv_path, index=False, encoding="utf-8-sig")
                            
                            is_up, _ = upload_to_gdrive(drive_service, csv_path, csv_name, gdrive_folder_id, 'text/csv')
                            if is_up:
                                st.success("🎉 任务完美结束！带有 AI 商业总结的报表已推送到网盘！")
                                os.remove(csv_path)
                            else:
                                st.warning("⚠️ CSV 上传失败，但已保存历史记录避免重复扣费。")
