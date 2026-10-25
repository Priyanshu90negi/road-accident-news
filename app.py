"""Streamlit interface for manual accident-news collection and review."""

from __future__ import annotations

from datetime import date

import streamlit as st

from database import db
from database.db import DuplicateArticleError
from services import article_service, export_service, extraction_service


st.set_page_config(page_title="Dehradun Accident News", page_icon="🚗", layout="wide")
db.initialize_database()


def _article_label(article: dict) -> str:
    return f"#{article['article_id']} · {article['published_date']} · {article['headline']}"


def _render_review(article_id: int) -> None:
    article = article_service.get_article(article_id)
    if not article:
        st.error("The selected article could not be found.")
        return
    records = db.list_accident_records(article_id)
    left, right = st.columns([0.9, 1.1], gap="large")
    with left:
        st.subheader("Original article")
        st.caption(f"{article['source_name']} · {article['published_date']} · {article['language']}")
        st.link_button("Open source article", article["source_url"])
        st.text_area(
            "Complete original article text",
            value=article["article_text"],
            height=560,
            disabled=True,
            key=f"original-article-{article_id}",
        )
    with right:
        st.subheader("Extracted accident records")
        if not records:
            st.info("No road-accident records were extracted. Check the article text and language, then add details manually if appropriate.")
            return
        for record in records:
            st.markdown(f"#### Accident record #{record['accident_id']}")
            if record["possible_duplicate"]:
                st.warning("Possible duplicate incident reported by another article. Compare the source before marking it verified.")
            st.caption(
                f"Scope: {record['scope_status']} · Confidence: {record['extraction_confidence']:.2f} · "
                f"Cause status: {record['cause_status'] or 'unknown'}"
            )
            with st.form(f"review-record-{record['accident_id']}"):
                c1, c2 = st.columns(2)
                with c1:
                    accident_date = st.text_input("Incident date (YYYY-MM-DD)", value=record["accident_date"] or "", key=f"date-{record['accident_id']}")
                    accident_time = st.text_input("Incident time (HH:MM)", value=record["accident_time"] or "", key=f"time-{record['accident_id']}")
                    district = st.text_input("District", value=record["district"] or "", key=f"district-{record['accident_id']}")
                    area = st.text_input("Area / locality", value=record["area"] or "", key=f"area-{record['accident_id']}")
                    accident_type = st.text_input("Accident type", value=record["accident_type"] or "", key=f"type-{record['accident_id']}")
                with c2:
                    cause = st.text_input("Cause (reported/suspected)", value=record["cause"] or "", key=f"cause-{record['accident_id']}")
                    vehicles = st.text_input("Vehicles involved", value=record["vehicles_involved"] or "", key=f"vehicles-{record['accident_id']}")
                    deaths = st.text_input("Deaths (blank means unknown)", value="" if record["deaths"] is None else str(record["deaths"]), key=f"deaths-{record['accident_id']}")
                    injuries = st.text_input("Injuries (blank means unknown)", value="" if record["injuries"] is None else str(record["injuries"]), key=f"injuries-{record['accident_id']}")
                    weather = st.text_input("Weather / road conditions", value=record["weather"] or "", key=f"weather-{record['accident_id']}")
                statuses = ["pending", "needs_review", "verified"]
                status = st.selectbox(
                    "Review status (mark verified only after checking the article)",
                    statuses,
                    index=statuses.index(record["review_status"]),
                    key=f"status-{record['accident_id']}",
                )
                submitted = st.form_submit_button("Save corrections")
            if submitted:
                try:
                    extraction_service.save_review(
                        record["accident_id"],
                        {
                            "accident_date": accident_date,
                            "accident_time": accident_time,
                            "district": district,
                            "area": area,
                            "accident_type": accident_type,
                            "cause": cause,
                            "vehicles_involved": vehicles,
                            "deaths": deaths,
                            "injuries": injuries,
                            "weather": weather,
                        },
                        review_status=status,
                    )
                    st.success("Corrections saved. Field provenance is recorded in the database.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))


st.title("Dehradun Road Accident News · 2025")
st.caption("Manually collect English or Hindi articles, review extracted facts, and export structured records.")
page = st.sidebar.radio("Workflow", ["Collect article", "Articles", "Review records", "CSV export"])

if page == "Collect article":
    st.header("Add a news article")
    with st.form("article-entry"):
        headline = st.text_input("News headline *")
        article_text = st.text_area("Full article text *", height=420, max_chars=None, help="Paste the complete article. The saved source text is kept unchanged.")
        col1, col2 = st.columns(2)
        with col1:
            source_name = st.text_input("Source name *")
            source_url = st.text_input("Original article URL *")
        with col2:
            published_date = st.date_input("Publication date *", value=date.today())
            language = st.selectbox("Language *", ["English", "Hindi"])
        submitted = st.form_submit_button("Save Article")
    if submitted:
        try:
            article_id = article_service.save_article(
                {
                    "headline": headline,
                    "article_text": article_text,
                    "source_name": source_name,
                    "source_url": source_url,
                    "published_date": published_date,
                    "language": language,
                }
            )
            st.session_state["last_saved_article_id"] = article_id
            records = extraction_service.extract_and_save_article(article_id)
            st.success(f"Article #{article_id} saved. Extracted {len(records)} accident record(s), all awaiting human review.")
        except DuplicateArticleError as error:
            st.error(f"{error} Open the existing article from the Articles page.")
        except ValueError as error:
            st.error(str(error))
    article_id = st.session_state.get("last_saved_article_id")
    if article_id:
        st.divider()
        _render_review(article_id)

elif page == "Articles":
    st.header("Collected articles")
    articles = article_service.list_articles()
    if not articles:
        st.info("No articles have been saved yet.")
    else:
        st.dataframe(articles, hide_index=True, use_container_width=True)
        article_ids = [row["article_id"] for row in articles]
        selected_id = st.selectbox(
            "Open an article",
            article_ids,
            format_func=lambda value: _article_label(next(row for row in articles if row["article_id"] == value)),
        )
        article = article_service.get_article(selected_id)
        st.subheader(article["headline"])
        st.caption(f"{article['source_name']} · {article['published_date']} · {article['language']}")
        st.link_button("Open source article", article["source_url"])
        st.text_area("Complete original article text", value=article["article_text"], height=520, disabled=True)

elif page == "Review records":
    st.header("Review and correct extractions")
    articles = article_service.list_articles()
    if not articles:
        st.info("Save an article first.")
    else:
        article_ids = [row["article_id"] for row in articles]
        default_id = st.session_state.get("last_saved_article_id")
        index = article_ids.index(default_id) if default_id in article_ids else 0
        selected_id = st.selectbox(
            "Article",
            article_ids,
            index=index,
            format_func=lambda value: _article_label(next(row for row in articles if row["article_id"] == value)),
        )
        if not db.list_accident_records(selected_id):
            if st.button("Run extraction"):
                records = extraction_service.extract_and_save_article(selected_id)
                st.success(f"Created {len(records)} accident record(s).")
                st.rerun()
        _render_review(selected_id)

elif page == "CSV export":
    st.header("Export structured accident records")
    export_choice = st.radio("Records to export", ["All records", "Verified records only"])
    verified_only = export_choice == "Verified records only"
    if st.button("Generate accident_records.csv"):
        output = export_service.export_csv(verified_only=verified_only)
        st.session_state["last_export_path"] = str(output)
        st.success(f"Wrote {output}")
    output_path = st.session_state.get("last_export_path")
    if output_path:
        try:
            with open(output_path, "rb") as export_file:
                data = export_file.read()
            st.download_button("Download accident_records.csv", data=data, file_name="accident_records.csv", mime="text/csv")
        except OSError as error:
            st.error(f"Could not read the generated CSV: {error}")
