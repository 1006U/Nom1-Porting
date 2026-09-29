/*
 * NOM 1 Android port launcher.
 *
 * This file is original glue code for this repository. It is copied into the
 * Apache-2.0 J2ME Loader checkout by tools/prepare_engine.py.
 */
package ru.woesss.j2me.installer;

import android.app.Activity;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;

import io.reactivex.Single;
import io.reactivex.android.schedulers.AndroidSchedulers;
import io.reactivex.schedulers.Schedulers;
import ru.playsoftware.j2meloader.applist.AppItem;
import ru.playsoftware.j2meloader.applist.AppListModel;
import ru.playsoftware.j2meloader.config.Config;
import ru.playsoftware.j2meloader.config.ProfileModel;
import ru.playsoftware.j2meloader.config.ProfilesManager;

public final class Nom1LauncherActivity extends Activity {
    private static final String ASSET_JAR = "nom1/nom1.jar";

    private TextView statusView;
    private AppInstaller installer;
    private AppListModel appListModel;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        statusView = new TextView(this);
        statusView.setGravity(Gravity.CENTER);
        statusView.setText("Preparing NOM 1...");
        setContentView(statusView);

        try {
            File workRoot = new File(Config.getEmulatorDir());
            if (!workRoot.exists() && !workRoot.mkdirs()) {
                throw new IOException("Cannot create runtime directory: " + workRoot);
            }

            File jar = copyBundledJar();
            appListModel = new AppListModel(getApplication());
            installer = new AppInstaller(
                    jar.getAbsolutePath(),
                    Uri.fromFile(jar),
                    getApplication(),
                    appListModel.getAppRepository()
            );
            installOrLaunch();
        } catch (Throwable t) {
            showError(t);
        }
    }

    private void installOrLaunch() {
        Single.<Integer>create(installer::loadInfo)
                .subscribeOn(Schedulers.io())
                .flatMap(status -> {
                    if (status == AppInstaller.STATUS_NEW
                            || status == AppInstaller.STATUS_NEWEST) {
                        return Single.<Integer>create(installer::install);
                    }

                    if (status == AppInstaller.STATUS_EQUAL
                            || status == AppInstaller.STATUS_OLDEST
                            || status == AppInstaller.STATUS_SUCCESS) {
                        return Single.just(status);
                    }

                    return Single.error(new IllegalStateException(
                            "Unsupported installer status: " + status));
                })
                .observeOn(AndroidSchedulers.mainThread())
                .subscribe(ignored -> launchGame(), this::showError);
    }

    private File copyBundledJar() throws IOException {
        File dir = new File(getFilesDir(), "nom1");
        if (!dir.exists() && !dir.mkdirs()) {
            throw new IOException("Cannot create NOM asset directory");
        }

        File output = new File(dir, "nom1.jar");
        try (InputStream in = getAssets().open(ASSET_JAR);
             FileOutputStream out = new FileOutputStream(output, false)) {
            byte[] buffer = new byte[16 * 1024];
            int count;
            while ((count = in.read(buffer)) >= 0) {
                out.write(buffer, 0, count);
            }
        }
        return output;
    }

    private void launchGame() {
        try {
            AppItem app = installer.getCurrentApp();
            if (app == null) {
                throw new IllegalStateException("NOM install completed without an app record");
            }

            ensureNomProfile(app);
            Config.startApp(this, app.getTitle(), app.getPathExt(), false);
            finish();
        } catch (Throwable t) {
            showError(t);
        }
    }

    private void ensureNomProfile(AppItem app) throws IOException {
        File configDir = new File(Config.getConfigsDir(), app.getPath());
        if (!configDir.exists() && !configDir.mkdirs()) {
            throw new IOException("Cannot create NOM profile directory: " + configDir);
        }

        ProfileModel profile = ProfilesManager.loadConfig(configDir);
        if (profile == null) {
            profile = new ProfileModel(configDir);
        }

        profile.screenWidth = 176;
        profile.screenHeight = 208;

        // Fill the entire Galaxy display. This intentionally stretches the
        // original J2ME canvas to the device aspect ratio so there are no
        // letterbox bars on tall Galaxy screens.
        profile.screenScaleType = 2;
        profile.screenScaleRatio = 100;
        profile.screenGravity = 2;
        profile.forceFullscreen = true;

        profile.showKeyboard = false;
        profile.touchInput = false;

        profile.screenFilter = false;
        profile.graphicsMode = 1;

        if (!ProfilesManager.saveConfig(profile)) {
            throw new IOException("Cannot save NOM runtime profile");
        }
    }

    private void showError(Throwable error) {
        error.printStackTrace();
        runOnUiThread(() -> statusView.setText(
                "NOM 1 could not start.\n\n"
                        + error.getClass().getSimpleName()
                        + ": "
                        + String.valueOf(error.getMessage())
        ));
    }
}
