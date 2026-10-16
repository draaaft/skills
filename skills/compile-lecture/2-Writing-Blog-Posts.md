# 如何撰写博客章节（compile-lecture 技能附件）

## 博客章节

博客章节是用户会发布到个人博客站的，偏向**技术分享、整理总结和内容科普**性质的文章。不能将知识点笔记与博客文章进行等同。

## 弄清架构

首先需要了解用户使用的是**什么博客系统**。
如果当前你在用户的博客工作目录中，则可以进行*只读探索*。探索时注意避免一次性读取大量内容，注意截断性阅读。
否则，你最好主动询问用户相关信息，以免盲目撰写。

## 弄清习惯

理想情况下，用户的博客工作目录中应该已经有类似的、已经**写好的博客文章**。
建议你*节选阅读*其中的部分，以进一步了解用户的博客组织形式、命名习惯、文章结构习惯和文章风格。
在写作时，最终目标是使得你撰写的部分与用户已有的文章“好像确实是同一个人写的”。

## 配图方式

博客文章有时是需要**配图**的。博客配图的存储范式及命名习惯同样可以参考用户已有的文章配图。

当博客章节需要引用关键配图时，*应当*先交由用户人工确认与人工加工，再引用进文章。

此技能特意提供了一个好用的脚本来实现这个“人机分工”，其一般用法：

```
python <此SKILL目录>/scripts/man_img.py <原图片路径> -o <输出目录> --purpose "<这张图的用途>" --rename "<建议的文件名>"
```

该脚本会弹出一个 GUI 窗口（阻塞直到用户操作完成），用户可以进行：
裁切、调整色阶、二次重命名、浏览或改换同目录的其他图片、填写对你说的附加说明，随后批准或拒绝。

脚本会输出你可读的结果，形如：

```
Opened GUI window for the user.
User has approved (OR rejected) this image (additional response: "foobar").
User didn't rechoose the source image "<path1>".  # OR User changed the source image from "<path1>" to "<path2>".
User cropped the image to (x, y, w, h).  # OR User didn't crop the image.
Saved to "<path>"!
```

你需要根据结果继续工作。
批准时，使用给出的路径作为博客引用的图片；用户改图时，以变更后的新图为准；拒绝时，参考附加说明调整策略。

<!-- End of skill compile-lecture attachment, written by Harry Huang -->
