// RuriYamlDumper — dump a Unity prefab or model (FBX / other binary model) into a
// self-contained folder of plain-text YAML assets that the Blender
// "RuriRipperImporter" add-on can read.
//
// Unity stores imported FBX models as binary, and a .prefab only references its
// meshes / materials / textures / clips by GUID.  This tool reproduces the manual
// "select sub-asset -> Ctrl+D" extraction for an entire prefab at once and makes
// the result portable: it copies every dependency into the output folder with a
// fresh GUID and re-points the instance at the copies, so the Blender importer
// resolves the whole model inside that one folder.
//
// It instantiates the asset and fully unpacks the prefab connection, then extracts
// and re-points:
//   * every Mesh (SkinnedMeshRenderer / MeshFilter)       -> Meshes/*.asset
//   * every Material on every Renderer                     -> Materials/*.mat
//   * every Texture referenced by those materials          -> Textures/*.<img>
//   * the Animator Avatar                                  -> *.asset
//   * the Animator Controller and every clip it drives     -> Controller/*.controller, Anim/*.anim
//   * any AnimationClip embedded in the model file itself  -> Anim/*.anim
// and finally saves the whole hierarchy as a flat .prefab.
//
// All asset writes are wrapped in Start/StopAssetEditing so Unity imports the
// whole batch once instead of re-importing per created asset (which otherwise
// stalls the editor for large characters).  The only assets that must round-trip
// through the importer mid-run are the textures and the controller (they are
// copied as files and then loaded back to be re-pointed), so those are copied in
// their own leading batch before the object assets are generated.
//
// Requires Edit > Project Settings > Editor > Asset Serialization = Force Text
// (the tool warns and aborts otherwise).
//
// Right-click a prefab/model in the Project window -> Ruri > Dump Model to YAML (for Blender).

using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

public static class RuriYamlDumper
{
    private const string MenuPath = "Assets/Ruri/Dump Model to YAML (for Blender)";

    [MenuItem(MenuPath, true)]
    private static bool ValidateDump()
    {
        foreach (var obj in Selection.objects)
        {
            var path = AssetDatabase.GetAssetPath(obj);
            if (!string.IsNullOrEmpty(path) && AssetDatabase.LoadMainAssetAtPath(path) is GameObject)
                return true;
        }
        return false;
    }

    [MenuItem(MenuPath)]
    private static void DumpSelected()
    {
        if (EditorSettings.serializationMode != SerializationMode.ForceText)
        {
            EditorUtility.DisplayDialog(
                "RuriYamlDumper",
                "Asset Serialization must be 'Force Text'\n" +
                "(Project Settings > Editor > Asset Serialization).",
                "OK");
            return;
        }

        var done = new List<string>();
        foreach (var obj in Selection.objects)
        {
            var path = AssetDatabase.GetAssetPath(obj);
            if (string.IsNullOrEmpty(path) || !(AssetDatabase.LoadMainAssetAtPath(path) is GameObject))
                continue;
            var outDir = Dump(path);
            if (outDir != null)
                done.Add(outDir);
        }

        if (done.Count > 0)
            Debug.Log("RuriYamlDumper: dumped " + done.Count + " asset(s):\n" + string.Join("\n", done));
        else
            Debug.LogWarning("RuriYamlDumper: nothing dumped (select prefab / FBX / model assets).");
    }

    /// <summary>Dump one prefab/model asset path to a sibling "&lt;name&gt;_yaml" folder. Returns the output folder.</summary>
    public static string Dump(string modelPath)
    {
        var dir = Path.GetDirectoryName(modelPath).Replace("\\", "/");
        var baseName = Path.GetFileNameWithoutExtension(modelPath);
        var outDir = dir + "/" + baseName + "_yaml";
        if (AssetDatabase.IsValidFolder(outDir))
            AssetDatabase.DeleteAsset(outDir);
        AssetDatabase.CreateFolder(dir, baseName + "_yaml");
        AssetDatabase.CreateFolder(outDir, "Meshes");
        AssetDatabase.CreateFolder(outDir, "Materials");
        AssetDatabase.CreateFolder(outDir, "Textures");
        AssetDatabase.CreateFolder(outDir, "Anim");

        var src = AssetDatabase.LoadMainAssetAtPath(modelPath) as GameObject;
        var inst = (GameObject)PrefabUtility.InstantiatePrefab(src);
        // Break the prefab connection so the saved prefab inlines the full
        // GameObject/Transform/Renderer hierarchy instead of a thin instance.
        PrefabUtility.UnpackPrefabInstance(inst, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);

        var animator = inst.GetComponentInChildren<Animator>(true);
        var controllerSource = animator != null
            ? animator.runtimeAnimatorController as AnimatorController
            : null;

        // Unique asset paths are tracked here rather than via GenerateUniqueAssetPath,
        // which cannot see the not-yet-imported siblings created within a batch.
        var used = new HashSet<string>();

        // Leading batch: copy the texture source images (which must be loaded back
        // as objects to re-point materials).  texMap: original texture -> local copy
        // (null = keep the original reference, e.g. built-in / model-embedded).
        var texMap = ExtractTextures(inst, outDir, used);

        // Main batch: generate the object assets and re-point the instance at them.
        var meshMap = new Dictionary<Mesh, Mesh>();
        var matMap = new Dictionary<Material, Material>();
        var clipMap = new Dictionary<AnimationClip, AnimationClip>();
        try
        {
            AssetDatabase.StartAssetEditing();

            foreach (var smr in inst.GetComponentsInChildren<SkinnedMeshRenderer>(true))
                smr.sharedMesh = ExtractMesh(smr.sharedMesh, outDir, meshMap, used);
            foreach (var mf in inst.GetComponentsInChildren<MeshFilter>(true))
                mf.sharedMesh = ExtractMesh(mf.sharedMesh, outDir, meshMap, used);

            foreach (var renderer in inst.GetComponentsInChildren<Renderer>(true))
            {
                var mats = renderer.sharedMaterials;
                for (var i = 0; i < mats.Length; i++)
                {
                    if (mats[i] != null)
                        mats[i] = ExtractMaterial(mats[i], outDir, matMap, texMap, used);
                }
                renderer.sharedMaterials = mats;
            }

            if (animator != null && animator.avatar != null)
                animator.avatar = ExtractAvatar(animator.avatar, outDir, used);

            // Clips the controller drives, plus any embedded directly in the model
            // file (covers a directly-selected FBX with no controller).
            if (controllerSource != null)
            {
                foreach (var clip in controllerSource.animationClips)
                {
                    if (clip != null && !clip.name.StartsWith("__preview"))
                        ExtractClip(clip, outDir, clipMap, used);
                }
            }
            foreach (var asset in AssetDatabase.LoadAllAssetsAtPath(modelPath))
            {
                if (asset is AnimationClip clip && !clip.name.StartsWith("__preview"))
                    ExtractClip(clip, outDir, clipMap, used);
            }
        }
        finally
        {
            AssetDatabase.StopAssetEditing();
        }

        // Controller (single asset): copy it and rewrite its motions to the local
        // clip copies so the animation graph resolves inside the dump folder.
        if (controllerSource != null && animator != null)
            ExtractController(animator, controllerSource, outDir, clipMap, used);

        // A humanoid (muscle) clip is undecodable without the Avatar's muscle referential --
        // the Blender importer would produce visibly empty actions. The usual cause is the
        // source FBX being imported with avatarSetup=CopyFromOther: Unity then exposes NO
        // Animator component on the model prefab at all, so there is no Avatar to extract
        // here. Warn loudly at dump time instead of shipping a silently-crippled dump.
        if ((animator == null || animator.avatar == null))
        {
            foreach (var clip in clipMap.Keys)
            {
                if (clip.humanMotion)
                {
                    Debug.LogWarning(
                        "RuriYamlDumper: '" + baseName + "' carries humanoid (muscle) clip '" + clip.name +
                        "' but no Animator/Avatar was found on the instance -- the body motion cannot be " +
                        "decoded from this dump. If the source is an FBX with Avatar Setup = 'Copy From " +
                        "Other Avatar', reimport it with 'Create From This Model' (or dump the model that " +
                        "owns the Avatar) and dump again.");
                    break;
                }
            }
        }

        // Flush the material/controller re-points, then save the flat prefab.
        AssetDatabase.SaveAssets();
        var prefabPath = outDir + "/" + baseName + ".prefab";
        PrefabUtility.SaveAsPrefabAsset(inst, prefabPath);
        Object.DestroyImmediate(inst);
        AssetDatabase.Refresh();

        var textureCount = 0;
        foreach (var copy in texMap.Values)
            if (copy != null)
                textureCount++;
        Debug.Log($"RuriYamlDumper: {baseName} -> {meshMap.Count} meshes, {matMap.Count} materials, " +
                  $"{textureCount} textures, {clipMap.Count} clips  ({outDir})");
        return outDir;
    }

    // Copy every source image referenced by the instance's materials into Textures/
    // in a single import batch, then load the copies back for re-pointing.  Only
    // standalone image assets are copied; built-in, render, and model-embedded
    // textures keep their original reference (recorded as a null entry).
    private static Dictionary<Texture, Texture> ExtractTextures(GameObject inst, string outDir, HashSet<string> used)
    {
        var texMap = new Dictionary<Texture, Texture>();
        var unique = new List<Texture>();
        foreach (var renderer in inst.GetComponentsInChildren<Renderer>(true))
        {
            foreach (var mat in renderer.sharedMaterials)
            {
                if (mat == null)
                    continue;
                foreach (var propName in mat.GetTexturePropertyNames())
                {
                    var tex = mat.GetTexture(propName);
                    if (tex == null || texMap.ContainsKey(tex))
                        continue;
                    texMap[tex] = null;   // mark seen; filled after a successful copy
                    var srcPath = AssetDatabase.GetAssetPath(tex);
                    if (!string.IsNullOrEmpty(srcPath)
                        && (srcPath.StartsWith("Assets/") || srcPath.StartsWith("Packages/"))
                        && AssetImporter.GetAtPath(srcPath) is TextureImporter)
                    {
                        unique.Add(tex);
                    }
                }
            }
        }
        if (unique.Count == 0)
            return texMap;

        var dest = new Dictionary<Texture, string>();
        try
        {
            AssetDatabase.StartAssetEditing();
            foreach (var tex in unique)
            {
                var srcPath = AssetDatabase.GetAssetPath(tex);
                var destPath = UniquePath(used, outDir + "/Textures", tex.name, Path.GetExtension(srcPath));
                if (AssetDatabase.CopyAsset(srcPath, destPath))
                    dest[tex] = destPath;
            }
        }
        finally
        {
            AssetDatabase.StopAssetEditing();
        }
        foreach (var kv in dest)
            texMap[kv.Key] = AssetDatabase.LoadAssetAtPath<Texture>(kv.Value);
        return texMap;
    }

    private static Mesh ExtractMesh(Mesh mesh, string outDir, Dictionary<Mesh, Mesh> map, HashSet<string> used)
    {
        if (mesh == null)
            return null;
        if (map.TryGetValue(mesh, out var copy))
            return copy;
        copy = Object.Instantiate(mesh);
        AssetDatabase.CreateAsset(copy, UniquePath(used, outDir + "/Meshes", mesh.name, ".asset"));
        map[mesh] = copy;
        return copy;
    }

    // Copy a material to a standalone .mat and re-point every texture slot at its
    // local copy, so the material resolves entirely inside the dump folder.
    private static Material ExtractMaterial(Material mat, string outDir,
        Dictionary<Material, Material> matMap, Dictionary<Texture, Texture> texMap, HashSet<string> used)
    {
        if (matMap.TryGetValue(mat, out var copy))
            return copy;
        copy = Object.Instantiate(mat);
        AssetDatabase.CreateAsset(copy, UniquePath(used, outDir + "/Materials", mat.name, ".mat"));
        matMap[mat] = copy;

        foreach (var propName in copy.GetTexturePropertyNames())
        {
            var tex = copy.GetTexture(propName);
            if (tex != null && texMap.TryGetValue(tex, out var local) && local != null)
                copy.SetTexture(propName, local);
        }
        EditorUtility.SetDirty(copy);
        return copy;
    }

    private static Avatar ExtractAvatar(Avatar avatar, string outDir, HashSet<string> used)
    {
        var copy = Object.Instantiate(avatar);
        AssetDatabase.CreateAsset(copy, UniquePath(used, outDir, avatar.name, ".asset"));
        return copy;
    }

    private static AnimationClip ExtractClip(AnimationClip clip, string outDir,
        Dictionary<AnimationClip, AnimationClip> map, HashSet<string> used)
    {
        if (map.TryGetValue(clip, out var copy))
            return copy;
        copy = Object.Instantiate(clip);
        AssetDatabase.CreateAsset(copy, UniquePath(used, outDir + "/Anim", clip.name, ".anim"));
        map[clip] = copy;
        return copy;
    }

    // Copy the Animator's controller and rewrite its motions to point at the local
    // clip copies (already generated in clipMap), then re-point the Animator.  A
    // single asset, so it is copied outside the main batch and loaded back directly.
    private static void ExtractController(Animator animator, AnimatorController source, string outDir,
        Dictionary<AnimationClip, AnimationClip> clipMap, HashSet<string> used)
    {
        var srcPath = AssetDatabase.GetAssetPath(source);
        if (string.IsNullOrEmpty(srcPath))
            return;
        AssetDatabase.CreateFolder(outDir, "Controller");
        var destPath = UniquePath(used, outDir + "/Controller", source.name, ".controller");
        if (!AssetDatabase.CopyAsset(srcPath, destPath))
            return;
        var copy = AssetDatabase.LoadAssetAtPath<AnimatorController>(destPath);
        if (copy == null)
            return;

        foreach (var layer in copy.layers)
            RemapStateMachine(layer.stateMachine, clipMap);
        EditorUtility.SetDirty(copy);
        animator.runtimeAnimatorController = copy;
    }

    private static void RemapStateMachine(AnimatorStateMachine stateMachine,
        Dictionary<AnimationClip, AnimationClip> clipMap)
    {
        foreach (var child in stateMachine.states)
            child.state.motion = RemapMotion(child.state.motion, clipMap);
        foreach (var child in stateMachine.stateMachines)
            RemapStateMachine(child.stateMachine, clipMap);
    }

    private static Motion RemapMotion(Motion motion, Dictionary<AnimationClip, AnimationClip> clipMap)
    {
        if (motion is AnimationClip clip)
            return clipMap.TryGetValue(clip, out var copy) ? copy : clip;
        if (motion is BlendTree tree)
        {
            var children = tree.children;
            for (var i = 0; i < children.Length; i++)
                children[i].motion = RemapMotion(children[i].motion, clipMap);
            tree.children = children;
        }
        return motion;
    }

    // Reserve a unique "<dir>/<name><ext>" among assets created this run.  The
    // output folder is freshly recreated, so tracking our own additions is enough.
    private static string UniquePath(HashSet<string> used, string dir, string name, string ext)
    {
        var stem = dir + "/" + Safe(name);
        var path = stem + ext;
        var index = 1;
        while (!used.Add(path))
        {
            path = stem + "_" + index + ext;
            index++;
        }
        return path;
    }

    private static string Safe(string name)
    {
        foreach (var c in Path.GetInvalidFileNameChars())
            name = name.Replace(c, '_');
        return name;
    }
}
