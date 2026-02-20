# CLAUDE.md


## プロジェクト概要


## ドキュメント参照ガイドライン


## 品質ガイドライン

- **Discussionで作成されたログ（sessions テーブルの lanes, final_judge 等）は絶対に変更しない。** Monitor や Planning など後段のエージェントは、Discussion が記録したデータを読み取り専用で参照すること。上書き・削除・改変は禁止。


## 重要ファイル