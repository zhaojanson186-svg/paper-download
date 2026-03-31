import streamlit as st
import os
import time
import pandas as pd

from config import DOWNLOAD_DIR, sanitize_filename
from engine_ai import init_ai_model, list_available_gemini_models, analyze_paper_with_ai, analyze_patent_with_ai
from engine_gdrive import get_gdrive_service, upload_to_gdrive, get_cloud_history, update_cloud_history

from engine_scraper import (search_pmc_oa, download_pdf, fetch_pmc_metadata, 
                            search_europe_pmc_patents, search_google_patents, 
                            get_last_patent_fetch_debug, download_fulltext_txt, 
                            download_patent_fulltext_txt, download_google_patent_fulltext_txt)

st.set_page_config(page_title="双擎 AI 情报终端", layout="wide", page_icon="🧠")

st.title("🧠 药物研发 AI 全景情报终端")
st.markdown("文献精读与专利防线双向覆盖。自动抓取、LLM 智能提纯、去重并直传云盘。")

if 'cloud_history' not in st.session_state:
    st.session_state['cloud_history'] = {}
if 'history_file_id' not in st.session_state:
    st.session_state['history_file_id'] = None
if 'is_history_loaded' not in st.session_state:
    st.session_state['is_history_loaded'] = False

with st.sidebar:
    st.header("⚙️ 全局配置")
    gdrive_folder_id = st.text_input("📁 Google Drive 文件夹 ID", placeholder="粘贴你的文件夹ID")
    st.markdown("---")
    gemini_model_name = st.text_input("🤖 Gemini model id", value="gemini-1.5-flash-latest")
    gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")
    gcp_token = st.secrets.get("GCP_TOKEN", "")

    if gcp_token and gdrive_folder_id and not st.session_state['is_history_loaded']:
        drive_service, _ = get_gdrive_service(gcp_token)
        if drive_service:
            with st.spinner("正在同步云端历史账本..."):
                h_data, h_id = get_cloud_history(drive_service, gdrive_folder_id)
                st.session_state['cloud_history'] = h_data
                st.session_state['history_file_id'] = h_id
                st.session_state['is_history_loaded'] = True
                st.success("云端账本挂载成功！")

    ai_model = init_ai_model(gemini_api_key, gemini_model_name)
    if ai_model: st.success("🤖 AI 双擎提纯：已激活")
    else: st.error("🤖 AI 双擎提纯：离线")

    st.markdown("---")
    debug_mode = st.checkbox("🧪 显示 AI 解析调试信息", value=False, key="debug_ai_parse")
    st.write(f"📖 云端总账本记录数: **{len(st.session_state['cloud_history'])}** 条")
    
    if st.button("🗑️ 清空历史记录", type="secondary"):
        if st.session_state['history_file_id']:
            drive_srv, _ = get_gdrive_service(gcp_token)
            if drive_srv:
                try: drive_srv.files().delete(fileId=st.session_state['history_file_id']).execute()
                except: pass
        st.session_state['cloud_history'] = {}
        st.session_state['history_file_id'] = None
        st.session_state['is_history_loaded'] = False
        st.success("云端账本已彻底重置！")
        time.sleep(1.5)
        st.rerun()

tab1, tab2 = st.tabs(["📄 核心文献直传 + AI 精读", "💡 专利雷达 + AI 构型拆解"])

with tab1:
    st.markdown("### 🧬 学术前沿：抓取原文并生成 AI 精读报表")
    query_paper = st.text_input("输入检索关键词", value="CD3 bispecific antibody", key="q_paper")
    max_papers = st.number_input("请求最大篇数", min_value=1, max_value=500, value=15)
    
    if st.button("🚀 开始极速抓取并进行 AI 提纯", type="primary"):
        if not query_paper or not gdrive_folder_id or not ai_model: st.error("配置不完整！")
        else:
            drive_service, err = get_gdrive_service(gcp_token)
            if not drive_service: st.error(f"网盘授权失败: {err}")
            else:
                history = st.session_state['cloud_history']
                all_pmc_ids = search_pmc_oa(query_paper, max_papers)
                new_pmc_ids = [pid for pid in all_pmc_ids if f"PMC_{pid}" not in history]
                
                if not new_pmc_ids: st.info("🔕 未发现新文献。")
                else:
                    progress_bar = st.progress(0)
                    paper_report_data = []
                    for i, pmcid in enumerate(new_pmc_ids):
                        status, local_path, file_name = download_pdf(pmcid, query_paper)
                        pdf_st = "未上传"
                        if status == "下载成功":
                            is_up, _ = upload_to_gdrive(drive_service, local_path, file_name, gdrive_folder_id, 'application/pdf')
                            pdf_st = "✅ PDF已入库" if is_up else "❌ 上传失败"
                            os.remove(local_path)
                        else:
                            txt_status, txt_path, txt_name = download_fulltext_txt(pmcid, query_paper, DOWNLOAD_DIR)
                            if txt_status == "备用抓取成功":
                                is_up, _ = upload_to_gdrive(drive_service, txt_path, txt_name, gdrive_folder_id, 'text/plain')
                                pdf_st = "✅ 网页TXT已入库" if is_up else "❌ 上传失败"
                                os.remove(txt_path)
                            else: pdf_st = f"❌ 失败: {txt_status}"

                        title, abstract = fetch_pmc_metadata(pmcid)
                        ai_insights = analyze_paper_with_ai(ai_model, abstract, debug_mode)
                        paper_report_data.append({
                            "文献编号": f"PMC{pmcid}", "🎯核心靶点": ai_insights.get("靶点组合", ""),
                            "🐁实验模型": ai_insights.get("实验模型", ""), "💡核心结论": ai_insights.get("AI核心结论", ""),
                            "原文状态": pdf_st, "文献标题": title, "官方链接": f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/"
                        })
                        history[f"PMC_{pmcid}"] = f"✅ 已精读 ({pdf_st})"
                        st.session_state['history_file_id'] = update_cloud_history(drive_service, gdrive_folder_id, history, file_id=st.session_state['history_file_id'])
                        time.sleep(4.5)
                        progress_bar.progress((i + 1) / len(new_pmc_ids))
                    st.dataframe(pd.DataFrame(paper_report_data), use_container_width=True, hide_index=True)

with tab2:
    st.markdown("### 🧠 竞争对手挖掘：大模型提炼核心管线情报")
    patent_source = st.radio("🔎 数据源策略：", ["🌍 Google Patents 模式 (全球广度+全文提取)", "🔬 Europe PMC 模式 (医药深度+全文提取)"])
    query_patent = st.text_input("输入检索关键词", value="CD3 bispecific antibody", key="q_patent")
    max_patents = st.number_input("专利数量", min_value=1, max_value=200, value=30)
    
    if st.button("📊 生成【专利 AI 提纯】报表并推送", type="primary"):
        if not query_patent or not gdrive_folder_id or not ai_model: st.error("配置不完整！")
        else:
            drive_service, err = get_gdrive_service(gcp_token)
            if not drive_service: st.error(f"网盘授权失败: {err}")
            else: 
                is_europe = "Europe PMC" in patent_source
                patents = search_europe_pmc_patents(query_patent, max_patents) if is_europe else search_google_patents(query_patent, max_patents)
                
                if not patents: st.warning("未抓取到数据。")
                else:
                    history = st.session_state['cloud_history']
                    new_patents = [pt for pt in patents if f"PAT_{pt['全球公开号']}" not in history]
                    if not new_patents: st.info("均已在账本中。")
                    else:
                        ai_progress = st.progress(0)
                        ai_status = st.empty()
                        for idx, pt in enumerate(new_patents):
                            ai_status.text(f"🤖 AI 提纯: {pt['全球公开号']} ...")
                            # 大模型 429 重试机制
                            ai_insights = {}
                            for _ in range(3):
                                ai_insights = analyze_patent_with_ai(ai_model, pt['核心摘要'], debug_mode)
                                if "429" not in str(ai_insights): break
                                st.warning("⚠️ 触发限流，冷静10秒...")
                                time.sleep(10.0)

                            pt["🎯靶点组合"] = ai_insights.get("靶点组合", "未提取")
                            pt["🧬抗体构型"] = ai_insights.get("抗体构型", "未提取")
                            pt["💡商业一句话总结"] = ai_insights.get("AI一句话总结", "未提取")

                            # 全文提取逻辑：根据模式调用不同函数
                            if is_europe:
                                txt_status, txt_path, txt_name = download_patent_fulltext_txt(pt['全球公开号'], query_patent, DOWNLOAD_DIR)
                            else:
                                txt_status, txt_path, txt_name = download_google_patent_fulltext_txt(pt['全球公开号'], query_patent, DOWNLOAD_DIR)
                            
                            txt_up = "未上传"
                            if txt_status == "抓取成功":
                                is_up, _ = upload_to_gdrive(drive_service, txt_path, txt_name, gdrive_folder_id, 'text/plain')
                                txt_up = "✅ 已入库" if is_up else "❌ 上传失败"
                                os.remove(txt_path)
                            else: txt_up = f"❌ 无全文({txt_status})"

                            history[f"PAT_{pt['全球公开号']}"] = f"✅ 已分析 ({txt_up})"
                            time.sleep(6.5)
                            ai_progress.progress((idx + 1) / len(new_patents))
                        
                        st.session_state['history_file_id'] = update_cloud_history(drive_service, gdrive_folder_id, history, file_id=st.session_state['history_file_id'])
                        st.dataframe(pd.DataFrame(new_patents), use_container_width=True, hide_index=True)
                        st.success("🎉 任务完美结束！")
