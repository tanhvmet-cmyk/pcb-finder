import sys
src, pdfjs, worker, out = sys.argv[1:5]
s = open(src, encoding="utf-8").read()
s = s.replace("<script>/*PDFJS*/</script>", "<script>" + open(pdfjs, encoding="utf-8").read() + "</script>", 1)
s = s.replace("/*WORKER*/", open(worker, encoding="utf-8").read(), 1)
open(out, "w", encoding="utf-8").write(s)
print(out, len(s))
