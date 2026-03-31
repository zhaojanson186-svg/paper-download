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
    
    gemini_model_name = st.text_input("🤖 初始 Gemini 模型", value="gemini-1.5-flash-latest")
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
        st.error("🤖 AI 双擎提纯：离线")

    st.markdown("---")
    debug_mode = st.checkbox("🧪 显示 AI 解析调试信息", value=False, key="debug_ai_parse")
    
    # 动态显示账本记录数
    record_count_placeholder = st.empty()
    record_count_placeholder.write(f"📖 云端总账本记录数: **{len(st.session_state['cloud_history'])}** 条")
    
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

# ---------------------------------------------------------
# 构建备用模型弹夹 (Model Chain)
# ---------------------------------------------------------
fallback_models = ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash-8b", "gemini-1.5-flash"]
model_chain = [gemini_model_name] + [m for m in fallback_models if m != gemini_model_name]


tab1, tab2 = st.tabs(["📄 核心文献直传 + AI 精读", "💡 专利雷达 + AI 构型拆解"])

with tab1:
    st.markdown("### 🧬 学术前沿：抓取原文并生成 AI 精读报表")
    query_paper = st.text_input("输入检索关键词 (靶点/适应症)", value="CD3 bispecific antibody", key="q_paper")
    max_papers = st.number_input("本次请求最大篇数", min_value=1, max_value=500, value=15)
    
    if st.button("🚀 开始极速抓取并进行 AI 提纯", type="primary"):
        if not query_paper or not gdrive_folder_id or not ai_model:
            st.error("请确保配置完整！")
        else:
            with st.spinner("正在初始化网盘与文献数据库..."):
                drive_service, err = get_gdrive_service(gcp_token)
            if not drive_service:
                st.error(f"网盘授权失败: {err}")
            else:
                history = st.session_state['cloud_history']
                all_pmc_ids = search_pmc_oa(query_paper, max_papers)
                new_pmc_ids = [pid for pid in all_pmc_ids if f"PMC_{pid}" not in history]
                
                if not new_pmc_ids:
                    st.info("🔕 本次未发现新的开源文献。")
                else:
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    result_container = st.empty() 
                    paper_report_data = []
                    current_model_idx = 0
                    
                    for i, pmcid in enumerate(new_pmc_ids):
                        status_text.text(f"🤖 处理第 {i+1}/{len(new_pmc_ids)} 篇 (PMC{pmcid})...")
                        
                        status, local_path, file_name = download_pdf(pmcid, query_paper)
                        pdf_uploaded = "未上传"
                        
                        if status == "下载成功":
                            is_up, _ = upload_to_gdrive(drive_service, local_path, file_name, gdrive_folder_id, 'application/pdf')
                            pdf_uploaded = "✅ PDF已入库" if is_up else "❌ 网盘上传失败"
                            os.remove(local_path)
                        else:
                            txt_status, txt_path, txt_name = download_fulltext_txt(pmcid, query_paper, DOWNLOAD_DIR)
                            if txt_status == "备用抓取成功":
                                is_up, _ = upload_to_gdrive(drive_service, txt_path, txt_name, gdrive_folder_id, 'text/plain')
                                pdf_uploaded = "✅ 网页TXT已入库" if is_up else "❌ TXT网盘上传失败"
                                os.remove(txt_path)
                            else:
                                pdf_uploaded = f"❌ 失败: {txt_status}"

                        title, abstract = fetch_pmc_metadata(pmcid)
                        
                        # ========================================================
                        # 【升级版】AI 无缝换模型装甲 (文献区) - 全字典扫描
                        # ========================================================
                        ai_insights = {}
                        while current_model_idx < len(model_chain):
                            success = False
                            for attempt in range(3):
                                ai_insights = analyze_paper_with_ai(ai_model, abstract, debug_mode)
                                
                                # 将整个字典强行转为小写字符串，地毯式搜索报错关键字
                                error_check = str(ai_insights).lower()
                                
                                if not any(kw in error_check for kw in ["429", "quota", "resourceexhausted", "too many requests", "503", "500"]):
                                    success = True
                                    break 
                                    
                                st.warning(f"⚠️ [{model_chain[current_model_idx]}] 触发限流，冷静 10 秒... (第 {attempt+1}/3 次重试)")
                                time.sleep(10.0)
                                
                            if success:
                                break 
                            else:
                                current_model_idx += 1
                                if current_model_idx < len(model_chain):
                                    new_model = model_chain[current_model_idx]
                                    st.error(f"🔴 当前模型额度耗尽！自动无缝切换至备用模型: {new_model} 🚀")
                                    ai_model = init_ai_model(gemini_api_key, new_model)
                                else:
                                    st.error("❌ 所有备用模型的每日免费额度均已耗尽！请明天再试。")
                                    ai_insights = {"靶点组合": "解析失败: 额度耗尽", "AI核心结论": "解析失败: 额度耗尽", "实验模型": "无"}
                                    break
                        # ========================================================
                        
                        paper_report_data.append({
                            "文献编号": f"PMC{pmcid}",
                            "🎯核心靶点": ai_insights.get("靶点组合", ""),
                            "🐁实验模型": ai_insights.get("实验模型", ""),
                            "💡核心结论": ai_insights.get("AI核心结论", ""),
                            "原文状态": pdf_uploaded,
                            "文献标题": title,
                            "官方链接": f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/"
                        })
                        
                        result_container.dataframe(pd.DataFrame(paper_report_data), column_config={"官方链接": st.column_config.LinkColumn()}, use_container_width=True, hide_index=True)
                        
                        history[f"PMC_{pmcid}"] = f"✅ 已精读 ({pdf_uploaded})"
                        st.session_state['cloud_history'] = history
                        st.session_state['history_file_id'] = update_cloud_history(drive_service, gdrive_folder_id, history, file_id=st.session_state['history_file_id'])
                        record_count_placeholder.write(f"📖 云端总账本记录数: **{len(history)}** 条")
                        
                        time.sleep(1.0)
                        progress_bar.progress((i + 1) / len(new_pmc_ids))
                        
                    if paper_report_data:
                        df_papers = pd.DataFrame(paper_report_data)
                        csv_name = f"{sanitize_filename(query_paper)}_Paper_AI_Report_{time.strftime('%m%d_%H%M')}.csv"
                        csv_path = os.path.join(DOWNLOAD_DIR, csv_name)
                        df_papers.to_csv(csv_path, index=False, encoding="utf-8-sig")
                        upload_to_gdrive(drive_service, csv_path, csv_name, gdrive_folder_id, 'text/csv')
                        st.success("🎉 任务完美结束！")
                        os.remove(csv_path)

with tab2:
    st.markdown("### 🧠 竞争对手挖掘：大模型提炼核心管线情报")
    
    patent_source = st.radio(
        "🔎 请选择数据源策略：",
        ["🌍 Google Patents 模式 (追求全球广度 + 全文说明书提取)", 
         "🔬 Europe PMC 模式 (追求医药深度 + 全文说明书提取)"],
        index=0
    )
    
    query_patent = st.text_input("输入检索关键词 (如靶点、技术全称)", value="CD3 bispecific antibody", key="q_patent")
    max_patents = st.number_input("需梳理的专利数量", min_value=1, max_value=200, value=30)
    
    if st.button("📊 生成【专利 AI 提纯】报表并推送", type="primary"):
        if not query_patent or not gdrive_folder_id or not ai_model:
            st.error("请确保配置完整！")
        else:
            with st.spinner("正在提取底层专利数据并比对账本..."):
                drive_service, err = get_gdrive_service(gcp_token)
                if not drive_service:
                    st.error(f"网盘授权失败: {err}")
                else: 
                    is_europe_mode = "Europe PMC" in patent_source
                    patents = search_europe_pmc_patents(query_patent, max_patents) if is_europe_mode else search_google_patents(query_patent, max_patents)
                    
                    if not patents:
                        debug = get_last_patent_fetch_debug()
                        st.warning(f"⚠️ 未能抓取到专利数据 (HTTP 状态码: {debug.get('status_code', '未知')})。")
                        if debug_mode: st.json(debug)
                    else:
                        history = st.session_state['cloud_history']
                        new_patents = [pt for pt in patents if f"PAT_{pt['全球公开号']}" not in history]
                        
                        if not new_patents:
                            st.info("🔕 扫描到的专利均已在历史账本中，无需重复提取！")
                        else:
                            ai_progress = st.progress(0)
                            ai_status = st.empty()
                            result_container_pat = st.empty()
                            refined_patent_data = []
                            current_model_idx = 0
                            
                            for idx, pt in enumerate(new_patents):
                                ai_status.text(f"🤖 AI 提纯第 {idx+1}/{len(new_patents)} 项: {pt['全球公开号']} ...")
                                
                                # ========================================================
                                # 【升级版】AI 无缝换模型装甲 (专利区) - 全字典扫描
                                # ========================================================
                                ai_insights = {}
                                while current_model_idx < len(model_chain):
                                    success = False
                                    for attempt in range(3):
                                        ai_insights = analyze_patent_with_ai(ai_model, pt['核心摘要'], debug_mode)
                                        
                                        error_check = str(ai_insights).lower()
                                        
                                        if not any(kw in error_check for kw in ["429", "quota", "resourceexhausted", "too many requests", "503", "500"]):
                                            success = True
                                            break
                                            
                                        st.warning(f"⚠️ [{model_chain[current_model_idx]}] 触发限流，冷静 10 秒... (第 {attempt+1}/3 次重试)")
                                        time.sleep(10.0)
                                        
                                    if success:
                                        break
                                    else:
                                        current_model_idx += 1
                                        if current_model_idx < len(model_chain):
                                            new_model = model_chain[current_model_idx]
                                            st.error(f"🔴 当前模型额度耗尽！自动无缝切换至备用模型: {new_model} 🚀")
                                            ai_model = init_ai_model(gemini_api_key, new_model)
                                        else:
                                            st.error("❌ 所有备用模型的每日免费额度均已耗尽！请明天再试。")
                                            ai_insights = {"靶点组合": "解析失败: 额度耗尽", "AI一句话总结": "解析失败: 额度耗尽", "抗体构型": "无"}
                                            break
                                # ========================================================

                                pt["🎯靶点组合"] = ai_insights.get("靶点组合", "未提取")
                                pt["🧬抗体构型"] = ai_insights.get("抗体构型", "未提取")
                                pt["💡商业一句话总结"] = ai_insights.get("AI一句话总结", "未提取")

                                txt_uploaded = "未上传"
                                
                                if is_europe_mode:
                                    txt_status, txt_path, txt_name = download_patent_fulltext_txt(pt['全球公开号'], query_patent, DOWNLOAD_DIR)
                                else:
                                    txt_status, txt_path, txt_name = download_google_patent_fulltext_txt(pt['全球公开号'], query_patent, DOWNLOAD_DIR)
                                
                                if txt_status == "抓取成功":
                                    is_up, _ = upload_to_gdrive(drive_service, txt_path, txt_name, gdrive_folder_id, 'text/plain')
                                    txt_uploaded = "✅ 已入网盘" if is_up else "❌ 网盘失败"
                                    os.remove(txt_path)
                                else:
                                    txt_uploaded = f"❌ 无正文 ({txt_status})"

                                pt["正文状态"] = txt_uploaded
                                refined_patent_data.append(pt)
                                
                                cols = ["全球公开号", "申请公司 / 拥有者", "🎯靶点组合", "🧬抗体构型", "💡商业一句话总结", "正文状态", "优先权/申请日", "专利名称", "核心摘要", "直达阅读链接"]
                                df_disp = pd.DataFrame(refined_patent_data)[cols]
                                result_container_pat.dataframe(df_disp, column_config={"直达阅读链接": st.column_config.LinkColumn()}, use_container_width=True, hide_index=True)

                                history[f"PAT_{pt['全球公开号']}"] = f"✅ 已AI提纯 ({txt_uploaded})"
                                st.session_state['cloud_history'] = history
                                st.session_state['history_file_id'] = update_cloud_history(drive_service, gdrive_folder_id, history, file_id=st.session_state['history_file_id'])
                                record_count_placeholder.write(f"📖 云端总账本记录数: **{len(history)}** 条")
                                
                                time.sleep(1.0) 
                                ai_progress.progress((idx + 1) / len(new_patents))
                            
                            if refined_patent_data:
                                csv_name = f"{sanitize_filename(query_patent)}_Patent_AI_Report_{time.strftime('%m%d_%H%M')}.csv"
                                csv_path = os.path.join(DOWNLOAD_DIR, csv_name)
                                df_disp.to_csv(csv_path, index=False, encoding="utf-8-sig")
                                upload_to_gdrive(drive_service, csv_path, csv_name, gdrive_folder_id, 'text/csv')
                                st.success("🎉 任务完美结束！带有 AI 商业总结的报表已推送到网盘！")
                                os.remove(csv_path)
