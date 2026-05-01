# Client Model Folder

The YOLO `.pt` model file is not included in this repository.

Download the model weights from:

```text
https://huggingface.co/doguilmak/Drone-Detection-YOLOv11x/tree/main/weight
```

The recommended first-start setup is to upload the downloaded `.pt` file through the central server Model Manager page. The client can then download the active model release from the server.

Optional local fallback: to run this client without downloading a model from the central server, place the downloaded `.pt` file here and name it:

```text
best_v11.pt
```

Expected path:

```text
client/models/best_v11.pt
```

`current_model.json` is runtime metadata and should not be committed.
